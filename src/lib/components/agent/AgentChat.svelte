<script lang="ts">
	import { getContext, onDestroy, onMount, tick } from 'svelte';
	import { goto } from '$app/navigation';
	import { toast } from 'svelte-sonner';
	import {
		downloadAgentFile,
		getAgentChat,
		getAgentEvents,
		getAgentFiles,
		getAgentStatus,
		interruptAgentChat,
		sendAgentMessage
	} from '$lib/apis/agentapi';
	import Markdown from '$lib/components/chat/Messages/Markdown.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import ArrowLeft from '$lib/components/icons/ArrowLeft.svelte';
	import ArrowDownTray from '$lib/components/icons/ArrowDownTray.svelte';
	import Stop from '$lib/components/icons/Stop.svelte';

	export let chatId: string;
	const i18n: any = getContext('i18n');

	let chat: any = null;
	let session: any = null;
	let events: any[] = [];
	let files: any[] = [];
	let message = '';
	let loading = true;
	let sending = false;
	let polling = false;
	let timer: ReturnType<typeof setTimeout> | null = null;
	let destroyed = false;
	let waitingForIdleAfter: number | null = null;
	let endMarker: HTMLDivElement;
	$: canSend = !chat?._open_webui?.read_only && session?.status !== 'terminated';

	const textOf = (event: any) => {
		const parts =
			event.content ?? event.input?.parts ?? event.output?.parts ?? event.message?.content ?? [];
		if (typeof parts === 'string') return parts;
		return (Array.isArray(parts) ? parts : [])
			.filter((part) => part?.type === 'text' || typeof part?.text === 'string')
			.map((part) => part.text)
			.join('\n');
	};

	const upsertEvents = (items: any[]) => {
		const byId = new Map(events.map((event) => [event.id, event]));
		for (const event of items) byId.set(event.id, event);
		events = [...byId.values()].sort((a, b) => (a.sequence_number ?? 0) - (b.sequence_number ?? 0));
	};

	const scheduleRefresh = (delay: number) => {
		if (timer) clearTimeout(timer);
		timer = setTimeout(() => {
			timer = null;
			void refresh();
		}, delay);
	};

	const syncEvents = async (full = false) => {
		let page: string | undefined;
		const watermark = full
			? undefined
			: (events.at(-1)?.created_at ?? events.at(-1)?.processed_at ?? undefined);
		do {
			const result = await getAgentEvents(localStorage.token, chatId, {
				page,
				createdAtGte: watermark,
				order: 'asc'
			});
			upsertEvents(result.data ?? []);
			page = result.next_page ?? undefined;
		} while (page && !destroyed);
		await tick();
		endMarker?.scrollIntoView({ behavior: full ? 'auto' : 'smooth' });
	};

	const refresh = async () => {
		if (polling || destroyed) return;
		if (timer) {
			clearTimeout(timer);
			timer = null;
		}
		polling = true;
		try {
			session = await getAgentStatus(localStorage.token, chatId);
			await syncEvents(false);
			if (
				waitingForIdleAfter !== null &&
				events.some(
					(event) =>
						event.type === 'session.status_idle' &&
						(event.sequence_number ?? 0) > waitingForIdleAfter!
				)
			) {
				waitingForIdleAfter = null;
			}
			const active = ['running', 'rescheduling', 'queued', 'pending'].includes(session?.status);
			if (!active) files = (await getAgentFiles(localStorage.token, chatId)).data ?? [];
			if ((active || waitingForIdleAfter !== null) && !destroyed) {
				scheduleRefresh(active && session.status === 'rescheduling' ? 3000 : 1500);
			}
		} catch (error) {
			if (!destroyed) scheduleRefresh(4000);
		} finally {
			polling = false;
		}
	};

	const submit = async () => {
		const content = message.trim();
		if (!content || sending || !canSend) return;
		sending = true;
		message = '';
		try {
			const ack = await sendAgentMessage(localStorage.token, chatId, content);
			const acceptedEvents = ack.data ?? [];
			upsertEvents(acceptedEvents);
			waitingForIdleAfter = Math.max(
				0,
				...events.map((event) => event.sequence_number ?? 0),
				...acceptedEvents.map((event: any) => event.sequence_number ?? 0)
			);
			session = { ...session, status: 'running' };
			await refresh();
		} catch (error) {
			message = content;
			toast.error(typeof error === 'string' ? error : $i18n.t('Failed to send message'));
		} finally {
			sending = false;
		}
	};

	const interrupt = async () => {
		try {
			await interruptAgentChat(localStorage.token, chatId);
			await refresh();
		} catch (error) {
			toast.error(typeof error === 'string' ? error : $i18n.t('Failed to interrupt Agent'));
		}
	};

	const resumePolling = () => {
		if (document.visibilityState === 'visible') void refresh();
	};

	onMount(async () => {
		window.addEventListener('focus', resumePolling);
		document.addEventListener('visibilitychange', resumePolling);
		try {
			chat = await getAgentChat(localStorage.token, chatId);
			await syncEvents(true);
			await refresh();
		} catch (error) {
			toast.error(typeof error === 'string' ? error : $i18n.t('Agent conversation not found'));
			await goto('/agent');
		} finally {
			loading = false;
		}
	});

	onDestroy(() => {
		destroyed = true;
		if (timer) clearTimeout(timer);
		window.removeEventListener('focus', resumePolling);
		document.removeEventListener('visibilitychange', resumePolling);
	});
</script>

<div class="flex h-screen max-h-[100dvh] w-full flex-col bg-white dark:bg-gray-950">
	<header
		class="flex h-14 shrink-0 items-center justify-between border-b border-gray-100 px-3 dark:border-gray-850 md:px-5"
	>
		<div class="flex min-w-0 items-center gap-3">
			<a
				href="/agent"
				class="rounded-lg p-2 hover:bg-gray-100 dark:hover:bg-gray-900"
				aria-label={$i18n.t('Back')}
			>
				<ArrowLeft className="size-4" />
			</a>
			<div class="min-w-0">
				<div class="truncate text-sm font-medium">{chat?.title ?? $i18n.t('Agent')}</div>
				<div class="flex items-center gap-1.5 text-[11px] text-gray-400">
					<span
						class="size-1.5 rounded-full {['running', 'rescheduling'].includes(session?.status)
							? 'animate-pulse bg-emerald-500'
							: 'bg-gray-300'}"
					></span>
					{session?.status ?? $i18n.t('Loading')}
					{#if chat?._open_webui?.read_only}<span>· {$i18n.t('Read only')}</span>{/if}
				</div>
			</div>
		</div>
		{#if chat?._open_webui?.is_owner && ['running', 'rescheduling'].includes(session?.status)}
			<button
				class="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-900"
				on:click={interrupt}
			>
				<Stop className="size-3.5" />
				{$i18n.t('Stop')}
			</button>
		{/if}
	</header>

	{#if loading}
		<div class="flex flex-1 items-center justify-center"><Spinner /></div>
	{:else}
		<div class="flex flex-1 overflow-hidden">
			<main class="flex min-w-0 flex-1 flex-col">
				<div class="flex-1 overflow-y-auto px-4 py-7">
					<div class="mx-auto flex max-w-3xl flex-col gap-5">
						{#each events as event (event.id)}
							{@const content = textOf(event)}
							{#if event.type === 'user.message'}
								<div
									class="ml-auto max-w-[85%] rounded-2xl rounded-br-md bg-gray-100 px-4 py-2.5 text-sm dark:bg-gray-850"
								>
									{content}
								</div>
							{:else if event.type === 'agent.message'}
								<article class="max-w-none text-sm leading-7 text-gray-800 dark:text-gray-100">
									<Markdown id={event.id} messageId={event.id} {content} />
								</article>
							{:else if event.type === 'agent.thinking' || event.type === 'agent.tool_use' || event.type === 'agent.tool_result'}
								<details
									class="rounded-xl border border-gray-100 px-3 py-2 text-xs text-gray-500 dark:border-gray-850"
								>
									<summary class="cursor-pointer font-medium"
										>{event.type.replace('agent.', '')}</summary
									>
									<pre class="mt-2 overflow-x-auto whitespace-pre-wrap">{content ||
											JSON.stringify(event, null, 2)}</pre>
								</details>
							{:else if event.type?.startsWith('session.status_')}
								<div class="flex items-center gap-2 text-[11px] text-gray-400">
									<span class="h-px flex-1 bg-gray-100 dark:bg-gray-850"></span>
									{event.type.replace('session.status_', '')}
									<span class="h-px flex-1 bg-gray-100 dark:bg-gray-850"></span>
								</div>
							{:else}
								<details class="text-xs text-gray-400">
									<summary class="cursor-pointer">{event.type}</summary>
									<pre class="mt-2 overflow-x-auto whitespace-pre-wrap">{JSON.stringify(
											event,
											null,
											2
										)}</pre>
								</details>
							{/if}
						{/each}
						<div bind:this={endMarker}></div>
					</div>
				</div>

				{#if canSend}
					<div class="shrink-0 px-4 pb-5">
						<div
							class="mx-auto flex max-w-3xl items-end gap-2 rounded-2xl border border-gray-200 bg-white p-2 shadow-sm dark:border-gray-700 dark:bg-gray-900"
						>
							<textarea
								id="agent-chat-input"
								bind:value={message}
								class="max-h-40 min-h-10 flex-1 resize-none bg-transparent px-2 py-2 text-sm outline-none"
								placeholder={$i18n.t('Message Agent')}
								on:keydown={(event) => {
									if (event.key === 'Enter' && !event.shiftKey) {
										event.preventDefault();
										submit();
									}
								}}
							></textarea>
							<button
								class="rounded-xl bg-gray-950 px-4 py-2 text-sm text-white disabled:opacity-40 dark:bg-white dark:text-black"
								disabled={!message.trim() || sending}
								on:click={submit}
							>
								{sending ? '…' : $i18n.t('Send')}
							</button>
						</div>
					</div>
				{/if}
			</main>

			{#if files.length > 0}
				<aside
					class="hidden w-72 shrink-0 overflow-y-auto border-l border-gray-100 p-4 dark:border-gray-850 lg:block"
				>
					<h2 class="mb-3 text-xs font-medium uppercase tracking-wider text-gray-400">
						{$i18n.t('Outputs')}
					</h2>
					<div class="flex flex-col gap-2">
						{#each files as file}
							<button
								class="flex items-center gap-2 rounded-xl border border-gray-100 p-3 text-left text-xs hover:bg-gray-50 dark:border-gray-800 dark:hover:bg-gray-900"
								on:click={() => downloadAgentFile(localStorage.token, chatId, file)}
							>
								<ArrowDownTray className="size-4 shrink-0" />
								<span class="min-w-0 flex-1 truncate">{file.filename ?? file.id}</span>
							</button>
						{/each}
					</div>
				</aside>
			{/if}
		</div>
	{/if}
</div>
