<script lang="ts">
	import { page } from '$app/stores';
	import AgentChat from '$lib/components/agent/AgentChat.svelte';
</script>

<AgentChat chatIdProp={$page.params.id} />
