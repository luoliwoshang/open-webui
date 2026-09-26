<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { toast } from 'svelte-sonner';
	import {
		createAgentChat,
		getAgentChats,
		getAgentProfile,
		sendAgentMessage,
		toggleAgentChatArchive
	} from '$lib/apis/agentapi';
	import { user } from '$lib/stores';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import SparklesSolid from '$lib/components/icons/SparklesSolid.svelte';
	import ArchiveBox from '$lib/components/icons/ArchiveBox.svelte';

	const i18n: any = getContext('i18n');
	let profile: any = null;
	let chats: any[] = [];
	let loading = true;
	let creating = false;
	let showArchived = false;
	let prompt = '';

	const loadChats = async () => {
		const result = await getAgentChats(localStorage.token, {
			userId: $user?.id,
			includeArchived: showArchived
		});
		chats = result.items ?? [];
	};

	const toggleArchive = async (chat: any) => {
		try {
			await toggleAgentChatArchive(localStorage.token, chat.id);
			await loadChats();
		} catch (error) {
			toast.error(typeof error === 'string' ? error : $i18n.t('Failed to update conversation'));
		}
	};

	const create = async () => {
		const content = prompt.trim();
		if (!content || creating) return;
		creating = true;
		try {
			const created = await createAgentChat(localStorage.token, content.slice(0, 60));
			const id = created._open_webui.chat.id;
			await sendAgentMessage(localStorage.token, id, content);
			await goto(`/a/${id}`);
		} catch (error) {
			toast.error(typeof error === 'string' ? error : $i18n.t('Failed to start Agent session'));
			creating = false;
		}
	};

	onMount(async () => {
		try {
			profile = await getAgentProfile(localStorage.token);
			await loadChats();
		} catch (error) {
			toast.error(typeof error === 'string' ? error : $i18n.t('Failed to load Agent sessions'));
		} finally {
			loading = false;
		}
	});
</script>

<div class="mx-auto flex h-full w-full max-w-5xl flex-col px-5 py-8 md:px-10">
	{#if loading}
		<div class="flex flex-1 items-center justify-center"><Spinner /></div>
	{:else}
		<header class="mb-8 flex items-start justify-between gap-4">
			<div>
				<div
					class="mb-2 flex items-center gap-2 text-xs font-medium uppercase tracking-[0.18em] text-gray-400"
				>
					<SparklesSolid className="size-3.5" /> Agent workspace
				</div>
				<h1 class="text-3xl font-semibold tracking-tight text-gray-950 dark:text-white">
					{profile?.name ?? $i18n.t('Agent conversations')}
				</h1>
				{#if profile?.description}<p class="mt-2 max-w-2xl text-sm text-gray-500">
						{profile.description}
					</p>{/if}
			</div>
		</header>

		{#if profile}
			<section
				class="mb-10 rounded-3xl border border-gray-100 bg-gray-50/70 p-4 shadow-sm dark:border-gray-800 dark:bg-gray-900/50 md:p-6"
			>
				<textarea
					id="agent-input"
					bind:value={prompt}
					class="min-h-28 w-full resize-none bg-transparent text-base outline-none placeholder:text-gray-400"
					placeholder={$i18n.t('Give the Agent a task…')}
					on:keydown={(event) => {
						if (event.key === 'Enter' && !event.shiftKey) {
							event.preventDefault();
							create();
						}
					}}
				></textarea>
				<div class="flex justify-end">
					<button
						class="rounded-full bg-gray-950 px-5 py-2 text-sm font-medium text-white transition hover:scale-[1.02] disabled:opacity-40 dark:bg-white dark:text-black"
						disabled={!prompt.trim() || creating}
						on:click={create}>{creating ? $i18n.t('Starting…') : $i18n.t('Start session')}</button
					>
				</div>
			</section>
		{:else}
			<div
				class="mb-10 rounded-2xl border border-dashed border-gray-200 p-6 text-sm text-gray-500 dark:border-gray-700"
			>
				{$i18n.t(
					'No Agent is currently available to you. Your previous conversations remain below.'
				)}
			</div>
		{/if}

		<section>
			<div class="mb-3 flex items-center justify-between gap-3">
				<h2 class="text-sm font-medium text-gray-700 dark:text-gray-300">
					{showArchived ? $i18n.t('All sessions') : $i18n.t('Recent sessions')}
				</h2>
				<button
					class="rounded-lg px-2.5 py-1.5 text-xs text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-900"
					on:click={async () => {
						showArchived = !showArchived;
						await loadChats();
					}}
				>
					{showArchived ? $i18n.t('Hide archived') : $i18n.t('Show archived')}
				</button>
			</div>
			{#if chats.length === 0}
				<div class="py-12 text-center text-sm text-gray-400">
					{$i18n.t('No Agent conversations yet')}
				</div>
			{:else}
				<div class="grid gap-2 sm:grid-cols-2">
					{#each chats as chat}
						<div
							class="group flex items-center gap-2 rounded-2xl border border-gray-100 p-2 transition hover:-translate-y-0.5 hover:shadow-md dark:border-gray-800"
						>
							<a href={`/a/${chat.id}`} class="min-w-0 flex-1 p-2">
								<div class="flex items-center gap-2">
									<div class="truncate text-sm font-medium text-gray-900 dark:text-white">
										{chat.title}
									</div>
									{#if chat.archived}
										<span
											class="rounded-full bg-gray-100 px-2 py-0.5 text-[10px] text-gray-500 dark:bg-gray-850"
										>
											{$i18n.t('Archived')}
										</span>
									{/if}
								</div>
								<div class="mt-2 flex items-center justify-between text-xs text-gray-400">
									<span>{chat.meta?.agentapi?.profile_name ?? 'Agent'}</span>
									<span>{new Date(chat.updated_at * 1000).toLocaleString()}</span>
								</div>
							</a>
							<button
								class="rounded-xl p-2 text-gray-400 transition hover:bg-gray-100 hover:text-gray-700 sm:opacity-0 sm:group-hover:opacity-100 sm:focus:opacity-100 dark:hover:bg-gray-850 dark:hover:text-gray-200"
								aria-label={chat.archived ? $i18n.t('Unarchive') : $i18n.t('Archive')}
								on:click={() => toggleArchive(chat)}
							>
								<ArchiveBox className="size-4" />
							</button>
						</div>
					{/each}
				</div>
			{/if}
		</section>
	{/if}
</div>
