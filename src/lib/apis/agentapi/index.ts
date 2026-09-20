import { WEBUI_API_BASE_URL } from '$lib/constants';

export type AccessGrant = {
	id?: string;
	principal_type: 'user' | 'group' | 'anyone';
	principal_id: string;
	permission: 'read' | 'write';
};

export type AgentProfile = {
	id: string;
	name: string;
	description: string;
	agent_id: string;
	agent_version: number;
	environment_id: string;
	enabled: boolean;
	access_grants: AccessGrant[];
};

export type AgentProfileSummary = Pick<AgentProfile, 'id' | 'name' | 'description'>;

export type AgentAPIConfig = {
	enabled: boolean;
	base_url: string;
	api_key_configured: boolean;
	profiles: AgentProfile[];
};

export type AgentAPIEvent = {
	type?: string;
	name?: string;
	[key: string]: unknown;
};

export type AgentChatRecord = {
	id: string;
	user_id: string;
	title: string;
	mode: 'agent';
	chat: {
		history?: { messages?: Record<string, unknown>; currentId?: string | null };
		[key: string]: unknown;
	};
	meta?: {
		agentapi?: {
			profile_id?: string;
			profile_name?: string;
			[key: string]: unknown;
		};
		[key: string]: unknown;
	};
	current_message_id?: string | null;
};

export type AgentStreamEvent = {
	type: 'message.accepted' | 'agent.event' | 'done' | 'error';
	content?: string;
	detail?: string;
	event?: AgentAPIEvent;
	message_id?: string;
	user_message_id?: string;
};

type APIError = {
	detail?: string | { msg?: string }[];
	message?: string;
};

const detail = (error: unknown) => {
	if (!error || typeof error !== 'object') return `${error}`;
	const apiError = error as APIError;
	if (Array.isArray(apiError.detail))
		return apiError.detail.map((item) => item.msg ?? `${item}`).join(', ');
	return apiError.detail ?? apiError.message ?? `${error}`;
};

const jsonRequest = async <T>(token: string, path: string, init: RequestInit = {}): Promise<T> => {
	const response = await fetch(`${WEBUI_API_BASE_URL}/agentapi${path}`, {
		...init,
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token ? { authorization: `Bearer ${token}` } : {}),
			...(init.headers ?? {})
		}
	});
	if (!response.ok) throw new Error(detail(await response.json().catch(() => ({}))));
	return response.json() as Promise<T>;
};

export const getAgentAPIConfig = (token: string) => jsonRequest<AgentAPIConfig>(token, '/config');

export const updateAgentAPIConfig = (
	token: string,
	config: {
		enabled: boolean;
		base_url: string;
		api_key: string;
		profiles: AgentProfile[];
	}
) =>
	jsonRequest<AgentAPIConfig>(token, '/config', { method: 'POST', body: JSON.stringify(config) });

export const verifyAgentAPIConfig = (
	token: string,
	config: { base_url?: string; api_key?: string }
) =>
	jsonRequest<{ status: boolean }>(token, '/config/verify', {
		method: 'POST',
		body: JSON.stringify(config)
	});

export const getAgentProfiles = (token: string) =>
	jsonRequest<AgentProfileSummary[]>(token, '/profiles');

export const createAgentChat = (token: string, profileId: string, title: string | null = null) =>
	jsonRequest<AgentChatRecord>(token, '/chats', {
		method: 'POST',
		body: JSON.stringify({ profile_id: profileId, title })
	});

export const interruptAgentChat = (token: string, chatId: string) =>
	jsonRequest<{ status: boolean }>(token, `/chats/${chatId}/interrupt`, { method: 'POST' });

export const streamAgentMessage = async (
	token: string,
	chatId: string,
	content: string,
	onEvent: (event: AgentStreamEvent) => void,
	signal?: AbortSignal,
	messageIds?: { message_id: string; response_message_id: string }
) => {
	const response = await fetch(`${WEBUI_API_BASE_URL}/agentapi/chats/${chatId}/messages`, {
		method: 'POST',
		headers: {
			Accept: 'application/x-ndjson',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		body: JSON.stringify({ content, ...(messageIds ?? {}) }),
		signal
	});
	if (!response.ok) throw new Error(detail(await response.json().catch(() => ({}))));
	if (!response.body) throw new Error('AgentAPI returned an empty stream');

	const reader = response.body.getReader();
	const decoder = new TextDecoder();
	let buffer = '';
	let done = false;
	do {
		const result = await reader.read();
		done = result.done;
		buffer += decoder.decode(result.value, { stream: !done });
		const lines = buffer.split('\n');
		buffer = lines.pop() ?? '';
		for (const line of lines) {
			if (line.trim()) onEvent(JSON.parse(line));
		}
	} while (!done);
	if (buffer.trim()) onEvent(JSON.parse(buffer));
};
