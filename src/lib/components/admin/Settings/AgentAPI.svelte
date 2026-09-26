<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { getAgentConfig, saveAgentConfig, verifyAgentConfig } from '$lib/apis/agentapi';
	import AccessControl from '$lib/components/workspace/common/AccessControl.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	const i18n: any = getContext('i18n');

	let loading = true;
	let saving = false;
	let verifying = false;
	let apiKeyConfigured = false;
	let accessGrants: any[] = [];
	let form = {
		enabled: false,
		base_url: 'https://agent.qiniuapi.com',
		api_key: '',
		profile: {
			name: 'Agent',
			description: '',
			agent_id: '',
			environment_id: '',
			enabled: true,
			access_grants: [] as any[]
		}
	};

	const payload = () => ({
		...form,
		api_key: form.api_key || null,
		profile: { ...form.profile, access_grants: accessGrants }
	});

	const load = async () => {
		const config = await getAgentConfig(localStorage.token);
		apiKeyConfigured = config.api_key_configured;
		form.enabled = config.enabled;
		form.base_url = config.base_url;
		if (config.profile) {
			form.profile = {
				name: config.profile.name,
				description: config.profile.description ?? '',
				agent_id: config.profile.agent_id,
				environment_id: config.profile.environment_id,
				enabled: config.profile.enabled,
				access_grants: []
			};
			accessGrants = config.profile.access_grants ?? [];
		}
		loading = false;
	};

	const save = async () => {
		saving = true;
		try {
			const config = await saveAgentConfig(localStorage.token, payload());
			apiKeyConfigured = config.api_key_configured;
			form.api_key = '';
			accessGrants = config.profile?.access_grants ?? accessGrants;
			toast.success($i18n.t('Agent settings saved'));
		} catch (error) {
			toast.error(typeof error === 'string' ? error : $i18n.t('Failed to save Agent settings'));
		} finally {
			saving = false;
		}
	};

	const verify = async () => {
		verifying = true;
		try {
			const result = await verifyAgentConfig(localStorage.token, payload());
			const upstreamAgent = result.agent?.data ?? result.agent;
			if (upstreamAgent?.name && form.profile.name === 'Agent')
				form.profile.name = upstreamAgent.name;
			toast.success(
				upstreamAgent?.version
					? $i18n.t('Connected. Current Agent version: {{version}}', {
							version: upstreamAgent.version
						})
					: $i18n.t('AgentAPI connection verified')
			);
		} catch (error) {
			toast.error(typeof error === 'string' ? error : $i18n.t('AgentAPI verification failed'));
		} finally {
			verifying = false;
		}
	};

	onMount(load);
</script>

<div class="flex h-full flex-col">
	<h2 class="mb-1 text-sm font-medium text-gray-900 dark:text-white">{$i18n.t('Agent')}</h2>
	<p class="mb-5 text-xs text-gray-500 dark:text-gray-400">
		{$i18n.t(
			'Configure the single Agent available to your organization. New sessions always use its latest published version.'
		)}
	</p>

	{#if loading}
		<div class="flex flex-1 items-center justify-center"><Spinner /></div>
	{:else}
		<form class="flex flex-col gap-5" on:submit|preventDefault={save}>
			<div
				class="flex items-center justify-between rounded-xl border border-gray-100 px-3 py-2.5 dark:border-gray-800"
			>
				<div>
					<div class="text-sm font-medium">{$i18n.t('Enable Agent mode')}</div>
					<div class="text-xs text-gray-500">
						{$i18n.t('Allow authorized employees to create Agent sessions')}
					</div>
				</div>
				<Switch bind:state={form.enabled} />
			</div>

			<div class="grid gap-3 md:grid-cols-2">
				<label class="flex flex-col gap-1 text-xs text-gray-500">
					{$i18n.t('Agent ID')}
					<input
						class="input-prose rounded-xl px-3 py-2 text-sm"
						bind:value={form.profile.agent_id}
						required
					/>
				</label>
				<label class="flex flex-col gap-1 text-xs text-gray-500">
					{$i18n.t('Environment ID')}
					<input
						class="input-prose rounded-xl px-3 py-2 text-sm"
						bind:value={form.profile.environment_id}
						required
					/>
				</label>
				<label class="flex flex-col gap-1 text-xs text-gray-500">
					{$i18n.t('Display name')}
					<input
						class="input-prose rounded-xl px-3 py-2 text-sm"
						bind:value={form.profile.name}
						required
					/>
				</label>
				<label class="flex flex-col gap-1 text-xs text-gray-500">
					{$i18n.t('API Key')}
					<input
						class="input-prose rounded-xl px-3 py-2 text-sm"
						type="password"
						bind:value={form.api_key}
						placeholder={apiKeyConfigured ? $i18n.t('Configured — enter to replace') : ''}
						required={!apiKeyConfigured}
					/>
				</label>
			</div>

			<label class="flex flex-col gap-1 text-xs text-gray-500">
				{$i18n.t('Description')}
				<textarea
					class="input-prose min-h-20 rounded-xl px-3 py-2 text-sm"
					bind:value={form.profile.description}
				></textarea>
			</label>

			<details class="rounded-xl border border-gray-100 px-3 py-2 dark:border-gray-800">
				<summary class="cursor-pointer text-sm">{$i18n.t('Advanced connection settings')}</summary>
				<label class="mt-3 flex flex-col gap-1 text-xs text-gray-500">
					{$i18n.t('Base URL')}
					<input
						class="input-prose rounded-xl px-3 py-2 text-sm"
						bind:value={form.base_url}
						required
					/>
				</label>
			</details>

			<div>
				<div class="mb-2 text-sm font-medium">{$i18n.t('Who can use this Agent')}</div>
				<AccessControl
					bind:accessGrants
					accessRoles={['read']}
					defaultPermission="read"
					shareOpen={false}
				/>
			</div>

			<div class="flex justify-end gap-2">
				<button
					type="button"
					class="rounded-xl border border-gray-200 px-4 py-2 text-sm hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-850"
					disabled={verifying}
					on:click={verify}
				>
					{verifying ? $i18n.t('Verifying…') : $i18n.t('Verify connection')}
				</button>
				<button
					type="submit"
					class="rounded-xl bg-gray-900 px-4 py-2 text-sm text-white hover:bg-black disabled:opacity-50 dark:bg-white dark:text-black"
					disabled={saving}
				>
					{saving ? $i18n.t('Saving…') : $i18n.t('Save')}
				</button>
			</div>
		</form>
	{/if}
</div>
