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
		getAgentProfiles,
		interruptAgentChat,
		streamAgentMessage
	} from '$lib/apis/agentapi';
	import type { AgentAPIEvent, AgentChatRecord, AgentProfileSummary } from '$lib/apis/agentapi';
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
			} else {
				chatId.set('');
				chatTitle.set($i18n.t('New Agent Chat'));
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
				await goto(`/a/${chat.id}`, { replaceState: true, noScroll: true, keepFocus: true });
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
