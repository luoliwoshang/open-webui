<script lang="ts">
	import { createEventDispatcher, getContext, onMount } from 'svelte';
	import type { i18n as i18nType } from 'i18next';
	import type { Writable } from 'svelte/store';
	import { v4 as uuidv4 } from 'uuid';
	import { toast } from 'svelte-sonner';

	import Switch from '$lib/components/common/Switch.svelte';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import AccessControlModal from '$lib/components/workspace/common/AccessControlModal.svelte';
	import AdminSettingRow from './AdminSettingRow.svelte';
	import AdminSettingSection from './AdminSettingSection.svelte';
	import { getBackendConfig } from '$lib/apis';
	import { config } from '$lib/stores';
	import {
		getAgentAPIConfig,
		updateAgentAPIConfig,
		verifyAgentAPIConfig
	} from '$lib/apis/agentapi';
	import type { AccessGrant, AgentProfile } from '$lib/apis/agentapi';

	const i18n = getContext<Writable<i18nType>>('i18n');
	const dispatch = createEventDispatcher();

	let loaded = false;
	let saving = false;
	let testing = false;
	let enabled = false;
	let baseUrl = 'https://agent.qiniuapi.com';
	let apiKey = '';
	let apiKeyConfigured = false;
	let profiles: AgentProfile[] = [];
	let accessProfileIndex: number | null = null;
	let accessGrants: AccessGrant[] = [];
	let showAccessControl = false;

	const addProfile = () => {
		profiles = [
			...profiles,
			{
				id: uuidv4(),
				name: 'New Agent',
				description: '',
				agent_id: '',
				agent_version: 1,
				environment_id: '',
				enabled: true,
				access_grants: []
			}
		];
	};

	const openAccess = (index: number) => {
		accessProfileIndex = index;
		accessGrants = structuredClone(profiles[index]?.access_grants ?? []);
		showAccessControl = true;
	};

	const save = async () => {
		saving = true;
		try {
			const result = await updateAgentAPIConfig(localStorage.token, {
				enabled,
				base_url: baseUrl,
				api_key: apiKey,
				profiles
			});
			enabled = result.enabled;
			baseUrl = result.base_url;
			apiKey = '';
			apiKeyConfigured = result.api_key_configured;
			profiles = result.profiles;
			config.set(await getBackendConfig());
			toast.success($i18n.t('AgentAPI settings updated'));
			dispatch('save');
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			saving = false;
		}
	};

	const testConnection = async () => {
		testing = true;
		try {
			await verifyAgentAPIConfig(localStorage.token, { base_url: baseUrl, api_key: apiKey });
			toast.success($i18n.t('AgentAPI connection verified'));
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			testing = false;
		}
	};

	onMount(async () => {
		try {
			const result = await getAgentAPIConfig(localStorage.token);
			enabled = result.enabled;
			baseUrl = result.base_url;
			apiKeyConfigured = result.api_key_configured;
			profiles = result.profiles ?? [];
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			loaded = true;
		}
	});
</script>

<AccessControlModal
	bind:show={showAccessControl}
	bind:accessGrants
	accessRoles={['read']}
	shareOpen={false}
	onChange={() => {
		if (accessProfileIndex !== null && profiles[accessProfileIndex]) {
			profiles[accessProfileIndex].access_grants = accessGrants;
			profiles = [...profiles];
		}
	}}
/>

<form class="flex h-full flex-col justify-between text-sm" on:submit|preventDefault={save}>
	<h2 class="mb-4 text-sm font-medium text-gray-900 dark:text-white">{$i18n.t('AgentAPI')}</h2>

	<div class="min-h-0 flex-1 overflow-y-auto pr-1.5 scrollbar-hover">
		{#if loaded}
			<AdminSettingSection first title={$i18n.t('Connection')}>
				<AdminSettingRow
					label={$i18n.t('Enable Agent Mode')}
					description={$i18n.t('Agent conversations use the native Qiniu AgentAPI protocol.')}
					let:labelId
				>
					<Switch bind:state={enabled} ariaLabelledbyId={labelId} />
				</AdminSettingRow>

				<label class="block">
					<span class="text-xs text-gray-600 dark:text-gray-400">{$i18n.t('Base URL')}</span>
					<input
						class="mt-1 h-8 w-full rounded-lg border border-gray-100 bg-transparent px-2 text-xs outline-hidden focus:border-blue-400 dark:border-gray-800"
						bind:value={baseUrl}
						required
					/>
				</label>

				<label class="block">
					<span class="text-xs text-gray-600 dark:text-gray-400">{$i18n.t('MaaS API Key')}</span>
					<SensitiveInput
						variant="settings"
						bind:value={apiKey}
						required={!apiKeyConfigured}
						placeholder={apiKeyConfigured
							? $i18n.t('Configured — leave blank to keep current key')
							: $i18n.t('Enter MaaS API Key')}
					/>
				</label>

				<div>
					<button
						type="button"
						class="rounded-lg border border-gray-200 px-3 py-1.5 text-xs hover:bg-gray-50 disabled:opacity-50 dark:border-gray-700 dark:hover:bg-gray-900"
						disabled={testing || (!apiKey && !apiKeyConfigured)}
						on:click={testConnection}
					>
						{testing ? $i18n.t('Testing...') : $i18n.t('Test connection')}
					</button>
				</div>
			</AdminSettingSection>

			<AdminSettingSection title={$i18n.t('Agent Profiles')}>
				<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600">
					{$i18n.t(
						'Each profile binds an Agent version to an Environment. Access can be granted to all users, groups, or individual users.'
					)}
				</p>

				{#each profiles as profile, index (profile.id)}
					<div class="rounded-xl border border-gray-100 p-3 dark:border-gray-800">
						<div class="mb-3 flex items-center justify-between gap-3">
							<input
								class="min-w-0 flex-1 bg-transparent text-sm font-medium outline-hidden"
								bind:value={profile.name}
								placeholder={$i18n.t('Profile name')}
								required
							/>
							<Switch bind:state={profile.enabled} ariaLabel={$i18n.t('Enable profile')} />
						</div>

						<div class="grid grid-cols-1 gap-2 md:grid-cols-2">
							<label class="text-[0.6875rem] text-gray-500">
								{$i18n.t('Agent ID')}
								<input
									class="mt-1 h-8 w-full rounded-lg border border-gray-100 bg-transparent px-2 text-xs dark:border-gray-800"
									bind:value={profile.agent_id}
									required
								/>
							</label>
							<label class="text-[0.6875rem] text-gray-500">
								{$i18n.t('Agent version')}
								<input
									class="mt-1 h-8 w-full rounded-lg border border-gray-100 bg-transparent px-2 text-xs dark:border-gray-800"
									type="number"
									min="1"
									bind:value={profile.agent_version}
									required
								/>
							</label>
							<label class="text-[0.6875rem] text-gray-500 md:col-span-2">
								{$i18n.t('Environment ID')}
								<input
									class="mt-1 h-8 w-full rounded-lg border border-gray-100 bg-transparent px-2 text-xs dark:border-gray-800"
									bind:value={profile.environment_id}
									required
								/>
							</label>
							<label class="text-[0.6875rem] text-gray-500 md:col-span-2">
								{$i18n.t('Description')}
								<input
									class="mt-1 h-8 w-full rounded-lg border border-gray-100 bg-transparent px-2 text-xs dark:border-gray-800"
									bind:value={profile.description}
								/>
							</label>
						</div>

						<div class="mt-3 flex items-center justify-between">
							<button
								type="button"
								class="text-xs text-blue-600 dark:text-blue-400"
								on:click={() => openAccess(index)}
							>
								{$i18n.t('Configure access')}
							</button>
							<button
								type="button"
								class="text-xs text-red-600 dark:text-red-400"
								on:click={() => (profiles = profiles.filter((_, itemIndex) => itemIndex !== index))}
							>
								{$i18n.t('Delete')}
							</button>
						</div>
					</div>
				{/each}

				<button
					type="button"
					class="rounded-lg border border-dashed border-gray-300 px-3 py-2 text-xs hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-900"
					on:click={addProfile}
				>
					+ {$i18n.t('Add Agent profile')}
				</button>
			</AdminSettingSection>
		{:else}
			<div class="text-xs text-gray-500">{$i18n.t('Loading...')}</div>
		{/if}
	</div>

	<div class="mt-4 flex justify-end">
		<button
			class="rounded-full bg-gray-900 px-4 py-1.5 text-xs text-white disabled:opacity-50 dark:bg-white dark:text-black"
			type="submit"
			disabled={!loaded || saving}
		>
			{saving ? $i18n.t('Saving...') : $i18n.t('Save')}
		</button>
	</div>
</form>
