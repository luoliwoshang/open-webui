<script lang="ts">
	import { goto } from '$app/navigation';
	import type { i18n as i18nType } from 'i18next';
	import { getContext, onDestroy, onMount, tick } from 'svelte';
	import type { Writable } from 'svelte/store';
	import { toast } from 'svelte-sonner';
	import { v4 as uuidv4 } from 'uuid';

	import { getChatById } from '$lib/apis/chats';
	import {
		createAgentChat,
		downloadAgentFile,
		getAgentFiles,
		getAgentProfiles,
		interruptAgentChat,
		streamAgentMessage
	} from '$lib/apis/agentapi';
	import type {
		AgentAPIEvent,
		AgentChatRecord,
		AgentFile,
		AgentProfileSummary
	} from '$lib/apis/agentapi';
	import Markdown from '$lib/components/chat/Messages/Markdown.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import { chatId, chatTitle, config, showSidebar, user } from '$lib/stores';
	import { refreshChatList } from '$lib/stores/chatList';
	import { createMessagesList } from '$lib/utils';

	export let chatIdProp: string | null = null;

	type AgentMessage = {
		id: string;
		role: 'user' | 'assistant';
		content: string;
		done?: boolean;
		error?: string | { content?: string };
		meta?: { agentapi?: { events?: AgentAPIEvent[] } };
		[key: string]: unknown;
	};

	const i18n = getContext<Writable<i18nType>>('i18n');
	let loaded = false;
	let chat: AgentChatRecord | null = null;
	let profiles: AgentProfileSummary[] = [];
	let selectedProfileId = '';
	let messages: AgentMessage[] = [];
	let prompt = '';
	let running = false;
	let interrupting = false;
	let activity: AgentAPIEvent[] = [];
	let files: AgentFile[] = [];
	let filesLoading = false;
	let downloadingFileId = '';
	let fileRefreshTimers: ReturnType<typeof setTimeout>[] = [];
	let messagesElement: HTMLDivElement;

	$: selectedProfile = profiles.find((profile) => profile.id === selectedProfileId);
	$: profileName = chat?.meta?.agentapi?.profile_name ?? selectedProfile?.name ?? $i18n.t('Agent');
	$: canUseAgentMode =
		$config?.features?.enable_agent_mode &&
		($user?.role === 'admin' || ($user?.permissions?.features?.agent_mode ?? false));
	$: readOnly = !!chat && (chat.user_id !== $user?.id || !canUseAgentMode);

	const scrollToBottom = async () => {
		await tick();
		if (messagesElement) messagesElement.scrollTop = messagesElement.scrollHeight;
	};

	const messageError = (error: AgentMessage['error']) =>
		typeof error === 'string' ? error : (error?.content ?? '');

	const formatFileSize = (size: number | null | undefined) => {
		if (typeof size !== 'number' || size < 0) return '';
		if (size < 1024) return `${size} B`;
		const units = ['KB', 'MB', 'GB', 'TB'];
		let value = size / 1024;
		let unit = 0;
		while (value >= 1024 && unit < units.length - 1) {
			value /= 1024;
			unit += 1;
		}
		return `${value >= 10 ? value.toFixed(0) : value.toFixed(1)} ${units[unit]}`;
	};

	const formatCreatedAt = (createdAt: string | null | undefined) => {
		if (!createdAt) return '';
		const timestamp = Date.parse(createdAt);
		return Number.isNaN(timestamp) ? createdAt : new Date(timestamp).toLocaleString();
	};

	const fileDetails = (file: AgentFile) =>
		[file.mime_type, formatFileSize(file.size_bytes), formatCreatedAt(file.created_at)]
			.filter(Boolean)
			.join(' · ');

	const clearFileRefreshTimers = () => {
		for (const timer of fileRefreshTimers) clearTimeout(timer);
		fileRefreshTimers = [];
	};

	const loadFiles = async (showError = true) => {
		if (!chat?.id) {
			files = [];
			return;
		}
		filesLoading = true;
		try {
			files = await getAgentFiles(localStorage.token, chat.id);
		} catch (error) {
			if (showError) toast.error(`${error}`);
		} finally {
			filesLoading = false;
		}
	};

	const refreshFilesAfterTurn = () => {
		clearFileRefreshTimers();
		void loadFiles(false);
		fileRefreshTimers = [2000, 5000].map((delay) => setTimeout(() => void loadFiles(false), delay));
	};

	const downloadFile = async (file: AgentFile) => {
		if (!chat?.id || !file.downloadable || downloadingFileId) return;
		downloadingFileId = file.id;
		let objectUrl = '';
		try {
			const blob = await downloadAgentFile(localStorage.token, chat.id, file.id);
			objectUrl = URL.createObjectURL(blob);
			const anchor = document.createElement('a');
			anchor.href = objectUrl;
			anchor.download = file.filename.split(/[/\\]/).pop() || 'download';
			document.body.appendChild(anchor);
			anchor.click();
			anchor.remove();
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			if (objectUrl) URL.revokeObjectURL(objectUrl);
			downloadingFileId = '';
		}
	};

	const load = async () => {
		loaded = false;
		try {
			profiles = canUseAgentMode ? await getAgentProfiles(localStorage.token) : [];
			selectedProfileId = profiles[0]?.id ?? '';

			if (chatIdProp) {
				chat = await getChatById(localStorage.token, chatIdProp);
				if (chat?.mode !== 'agent') {
					await goto(chat ? `/c/${chat.id}` : '/');
					return;
				}
				chatId.set(chat.id);
				chatTitle.set(chat.title);
				selectedProfileId = chat?.meta?.agentapi?.profile_id ?? selectedProfileId;
				const history = chat?.chat?.history ?? { messages: {}, currentId: null };
				messages = createMessagesList(
					history,
					chat?.current_message_id ?? history.currentId
				) as AgentMessage[];
				const lastAssistant = [...messages]
					.reverse()
					.find((message) => message.role === 'assistant');
				activity = lastAssistant?.meta?.agentapi?.events ?? [];
				await loadFiles(false);
			} else {
				chatId.set('');
				chatTitle.set($i18n.t('New Agent Chat'));
				files = [];
			}
		} catch (error) {
			toast.error(`${error}`);
			if (chatIdProp) await goto('/agent');
		} finally {
			loaded = true;
			scrollToBottom();
		}
	};

	const send = async () => {
		const content = prompt.trim();
		if (!content || running || readOnly) return;
		if (!selectedProfileId && !chat) {
			toast.error($i18n.t('Select an Agent profile'));
			return;
		}

		running = true;
		activity = [];
		prompt = '';
		try {
			if (!chat) {
				chat = await createAgentChat(localStorage.token, selectedProfileId, content.slice(0, 80));
				chatId.set(chat.id);
				chatTitle.set(chat.title);
				// Keep this component (and its response stream) alive while giving the
				// newly created conversation its permanent URL.
				window.history.replaceState(history.state, '', `/a/${chat.id}`);
				await refreshChatList(localStorage.token, { refreshPinned: true });
			}

			const userMessageId = uuidv4();
			const responseMessageId = uuidv4();
			messages = [
				...messages,
				{ id: userMessageId, role: 'user', content, done: true },
				{ id: responseMessageId, role: 'assistant', content: '', done: false }
			];
			scrollToBottom();

			await streamAgentMessage(
				localStorage.token,
				chat.id,
				content,
				(event) => {
					const index = messages.findIndex((message) => message.id === responseMessageId);
					if (event.type === 'agent.event') {
						if (index !== -1) {
							messages[index] = {
								...messages[index],
								content: event.content ?? messages[index].content
							};
							messages = [...messages];
						}
						const type = typeof event.event?.type === 'string' ? event.event.type : '';
						if (
							type.startsWith('agent.tool') ||
							type.startsWith('agent.mcp_tool') ||
							type.startsWith('session.')
						) {
							if (event.event) activity = [...activity.slice(-19), event.event];
						}
					} else if (event.type === 'done' && index !== -1) {
						messages[index] = { ...messages[index], content: event.content ?? '', done: true };
						messages = [...messages];
					} else if (event.type === 'error') {
						if (index !== -1) {
							messages[index] = { ...messages[index], done: true, error: event.detail };
							messages = [...messages];
						}
						toast.error(event.detail ?? $i18n.t('AgentAPI request failed'));
					}
					scrollToBottom();
				},
				undefined,
				{ message_id: userMessageId, response_message_id: responseMessageId }
			);
			await refreshChatList(localStorage.token, { refreshPinned: true });
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			running = false;
			interrupting = false;
			if (chat?.id) refreshFilesAfterTurn();
			scrollToBottom();
		}
	};

	const interrupt = async () => {
		if (!chat?.id || !running || interrupting) return;
		interrupting = true;
		try {
			await interruptAgentChat(localStorage.token, chat.id);
		} catch (error) {
			toast.error(`${error}`);
			interrupting = false;
		}
	};

	onMount(load);
	onDestroy(() => {
		clearFileRefreshTimers();
		if ($chatId === chat?.id) {
			chatId.set('');
			chatTitle.set('');
		}
	});
</script>

<div
	class="flex h-full min-h-0 flex-col bg-white text-gray-900 dark:bg-gray-950 dark:text-gray-100"
>
	<header
		class="flex h-12 shrink-0 items-center justify-between border-b border-gray-100 px-3 dark:border-gray-900"
	>
		<div class="flex min-w-0 items-center gap-2">
			<button
				class="md:hidden"
				aria-label={$i18n.t('Open Sidebar')}
				on:click={() => showSidebar.set(true)}>☰</button
			>
			<div class="truncate text-sm font-medium">{chat?.title ?? $i18n.t('New Agent Chat')}</div>
			<span
				class="rounded-full bg-violet-100 px-2 py-0.5 text-[0.625rem] font-medium text-violet-700 dark:bg-violet-950 dark:text-violet-300"
				>Agent</span
			>
		</div>
		<div class="truncate text-xs text-gray-400">{profileName}</div>
	</header>

	{#if !loaded}
		<div class="flex flex-1 items-center justify-center"><Spinner className="size-5" /></div>
	{:else if !chat && !canUseAgentMode}
		<div class="m-auto max-w-md px-6 text-center text-sm text-gray-500">
			{$i18n.t('Agent mode is not enabled for this account.')}
		</div>
	{:else}
		<div bind:this={messagesElement} class="flex-1 overflow-y-auto px-4 py-6">
			<div class="mx-auto flex w-full max-w-3xl flex-col gap-6">
				{#if !chat && messages.length === 0}
					<div class="py-12 text-center">
						<div class="text-2xl font-semibold">{$i18n.t('Start an Agent task')}</div>
						<div class="mt-2 text-sm text-gray-500">
							{$i18n.t('This conversation will stay in Agent mode.')}
						</div>
						{#if profiles.length > 0}
							<select
								class="mt-6 rounded-xl border border-gray-200 bg-transparent px-3 py-2 text-sm dark:border-gray-800"
								bind:value={selectedProfileId}
							>
								{#each profiles as profile}
									<option value={profile.id}>{profile.name}</option>
								{/each}
							</select>
							{#if selectedProfile?.description}<p
									class="mx-auto mt-3 max-w-lg text-xs text-gray-400"
								>
									{selectedProfile.description}
								</p>{/if}
						{:else}
							<p class="mt-6 text-sm text-gray-500">
								{$i18n.t('No Agent profiles are available.')}
							</p>
						{/if}
					</div>
				{/if}

				{#each messages as message (message.id)}
					<div class={message.role === 'user' ? 'flex justify-end' : 'flex justify-start'}>
						<div
							class={message.role === 'user'
								? 'max-w-[85%] rounded-2xl bg-gray-100 px-4 py-2.5 text-sm dark:bg-gray-900'
								: 'w-full text-sm'}
						>
							{#if message.role === 'assistant'}
								{#if message.content}
									<Markdown
										id={message.id}
										chatId={chat?.id ?? ''}
										messageId={message.id}
										content={message.content}
										done={message.done !== false}
									/>
								{:else if message.done === false}
									<div class="flex items-center gap-2 text-gray-400">
										<Spinner className="size-4" />
										{$i18n.t('Agent is working...')}
									</div>
								{/if}
								{#if message.error}<div class="mt-2 text-xs text-red-500">
										{messageError(message.error)}
									</div>{/if}
							{:else}
								<div class="whitespace-pre-wrap">{message.content}</div>
							{/if}
						</div>
					</div>
				{/each}

				{#if running && activity.length > 0}
					<details
						class="rounded-xl border border-gray-100 px-3 py-2 text-xs text-gray-500 dark:border-gray-900"
					>
						<summary class="cursor-pointer"
							>{$i18n.t('Agent activity')} · {activity[activity.length - 1]?.type}</summary
						>
						<div class="mt-2 space-y-1 font-mono text-[0.6875rem]">
							{#each activity as event}<div>
									{event.type}{event.name ? ` · ${event.name}` : ''}
								</div>{/each}
						</div>
					</details>
				{/if}

				{#if chat}
					<section
						class="rounded-xl border border-gray-100 bg-gray-50/60 p-3 dark:border-gray-900 dark:bg-gray-900/30"
					>
						<div class="flex items-center justify-between gap-3">
							<div class="text-xs font-medium">{$i18n.t('Session files')}</div>
							<button
								type="button"
								class="text-xs text-gray-500 hover:text-gray-900 disabled:opacity-50 dark:hover:text-gray-100"
								disabled={filesLoading}
								on:click={() => loadFiles()}
							>
								{filesLoading ? $i18n.t('Refreshing...') : $i18n.t('Refresh')}
							</button>
						</div>

						{#if files.length > 0}
							<div class="mt-2 divide-y divide-gray-200 dark:divide-gray-800">
								{#each files as file (file.id)}
									<div class="flex items-center gap-3 py-2">
										<div class="min-w-0 flex-1">
											<div class="truncate text-xs font-medium" title={file.filename}>
												{file.filename}
											</div>
											{#if fileDetails(file)}
												<div class="mt-0.5 truncate text-[0.6875rem] text-gray-400">
													{fileDetails(file)}
												</div>
											{/if}
										</div>
										<button
											type="button"
											class="shrink-0 rounded-lg border border-gray-200 px-2.5 py-1 text-xs hover:bg-white disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-800 dark:hover:bg-gray-900"
											disabled={!file.downloadable || !!downloadingFileId}
											on:click={() => downloadFile(file)}
										>
											{downloadingFileId === file.id
												? $i18n.t('Downloading...')
												: file.downloadable
													? $i18n.t('Download')
													: $i18n.t('Unavailable')}
										</button>
									</div>
								{/each}
							</div>
						{:else if !filesLoading}
							<p class="mt-2 text-xs text-gray-400">
								{$i18n.t('No files yet. Ask the Agent to save deliverables in')}
								<code>/mnt/session/outputs/</code>.
							</p>
						{/if}
					</section>
				{/if}
			</div>
		</div>

		<div class="shrink-0 px-4 pb-4">
			<div class="mx-auto max-w-3xl">
				{#if readOnly}
					<div
						class="rounded-xl border border-gray-200 px-4 py-3 text-center text-xs text-gray-500 dark:border-gray-800"
					>
						{chat?.user_id !== $user?.id
							? $i18n.t('You are viewing this employee Agent conversation in read-only mode.')
							: $i18n.t('Agent mode is disabled for this account. This conversation is read-only.')}
					</div>
				{:else}
					<div
						class="flex items-end gap-2 rounded-2xl border border-gray-200 bg-white p-2 shadow-sm dark:border-gray-800 dark:bg-gray-900"
					>
						<textarea
							class="max-h-48 min-h-10 flex-1 resize-none bg-transparent px-2 py-2 text-sm outline-hidden"
							bind:value={prompt}
							placeholder={$i18n.t('Give the Agent a task...')}
							disabled={running || (!chat && profiles.length === 0)}
							on:keydown={(event) => {
								if (event.key === 'Enter' && !event.shiftKey) {
									event.preventDefault();
									send();
								}
							}}
						></textarea>
						{#if running}
							<button
								type="button"
								class="rounded-full bg-red-600 px-3 py-2 text-xs text-white disabled:opacity-50"
								disabled={interrupting}
								on:click={interrupt}
							>
								{interrupting ? $i18n.t('Stopping...') : $i18n.t('Stop')}
							</button>
						{:else}
							<button
								type="button"
								class="rounded-full bg-gray-900 px-4 py-2 text-xs text-white disabled:opacity-50 dark:bg-white dark:text-black"
								disabled={!prompt.trim() || (!chat && !selectedProfileId)}
								on:click={send}
							>
								{$i18n.t('Send')}
							</button>
						{/if}
					</div>
				{/if}
				<p class="mt-2 text-center text-[0.625rem] text-gray-400">
					{$i18n.t(
						'Agent conversations run through Qiniu AgentAPI and cannot switch to Chat mode.'
					)}
				</p>
			</div>
		</div>
	{/if}
</div>
