<script lang="ts">
	import { getContext } from 'svelte';
	import { getAgentChats } from '$lib/apis/agentapi';
	import ChatsModal from '$lib/components/layout/ChatsModal.svelte';

	const i18n: any = getContext('i18n');
	export let show = false;
	export let user: any;

	let chatList: any[] | null = null;
	let page = 1;
	let allChatsLoaded = false;
	let chatListLoading = false;

	const load = async (reset = false) => {
		if (!show) return;
		if (reset) {
			page = 1;
			chatList = null;
		}
		chatListLoading = true;
		const result = await getAgentChats(localStorage.token, {
			userId: user.id,
			includeArchived: true,
			skip: (page - 1) * 50,
			limit: 50
		});
		const items = result.items ?? [];
		chatList = reset || chatList === null ? items : [...chatList, ...items];
		allChatsLoaded = (chatList?.length ?? 0) >= result.total;
		chatListLoading = false;
	};

	const loadMore = async () => {
		page += 1;
		await load();
	};

	$: if (show) load(true);
	$: if (!show) {
		chatList = null;
		page = 1;
	}
</script>

<ChatsModal
	bind:show
	title={$i18n.t("{{user}}'s Agent conversations", {
		user: user.name.length > 32 ? `${user.name.slice(0, 32)}...` : user.name
	})}
	emptyPlaceholder={$i18n.t('No Agent conversations found for this user.')}
	chatPathPrefix="/a"
	readOnly={true}
	showSearch={false}
	{chatList}
	{allChatsLoaded}
	{chatListLoading}
	loadHandler={loadMore}
></ChatsModal>
