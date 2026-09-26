import { WEBUI_API_BASE_URL } from '$lib/constants';

const request = async (token: string, path: string, init: RequestInit = {}) => {
	const response = await fetch(`${WEBUI_API_BASE_URL}/agentapi${path}`, {
		...init,
		headers: {
			Accept: 'application/json',
			...(init.body ? { 'Content-Type': 'application/json' } : {}),
			Authorization: `Bearer ${token}`,
			...init.headers
		}
	});
	if (!response.ok) {
		const error = await response.json().catch(() => ({ detail: response.statusText }));
		throw error.detail ?? error;
	}
	return response.status === 204 ? null : response.json();
};

export const getAgentConfig = (token: string) => request(token, '/config');

export const saveAgentConfig = (token: string, config: Record<string, unknown>) =>
	request(token, '/config', { method: 'POST', body: JSON.stringify(config) });

export const verifyAgentConfig = (token: string, config: Record<string, unknown>) =>
	request(token, '/config/verify', { method: 'POST', body: JSON.stringify(config) });

export const getAgentProfile = async (token: string) => {
	try {
		return await request(token, '/profile');
	} catch {
		return null;
	}
};

export const getAgentChats = (
	token: string,
	options: { userId?: string; includeArchived?: boolean; skip?: number; limit?: number } = {}
) => {
	const params = new URLSearchParams();
	if (options.userId) params.set('user_id', options.userId);
	if (options.includeArchived) params.set('include_archived', 'true');
	params.set('skip', String(options.skip ?? 0));
	params.set('limit', String(options.limit ?? 50));
	return request(token, `/chats?${params.toString()}`);
};

export const createAgentChat = (
	token: string,
	title: string,
	metadata: Record<string, unknown> = {}
) =>
	request(token, '/chats', {
		method: 'POST',
		body: JSON.stringify({ title, metadata })
	});

export const getAgentChat = (token: string, chatId: string) => request(token, `/chats/${chatId}`);

export const getAgentStatus = (token: string, chatId: string) =>
	request(token, `/chats/${chatId}/status`);

export const getAgentEvents = (
	token: string,
	chatId: string,
	options: { page?: string; limit?: number; order?: 'asc' | 'desc'; createdAtGte?: string } = {}
) => {
	const params = new URLSearchParams({
		limit: String(options.limit ?? 100),
		order: options.order ?? 'asc'
	});
	if (options.page) params.set('page', options.page);
	if (options.createdAtGte) params.set('created_at_gte', options.createdAtGte);
	return request(token, `/chats/${chatId}/events?${params.toString()}`);
};

export const sendAgentMessage = (token: string, chatId: string, content: string) =>
	request(token, `/chats/${chatId}/messages`, {
		method: 'POST',
		body: JSON.stringify({ content })
	});

export const interruptAgentChat = (token: string, chatId: string) =>
	request(token, `/chats/${chatId}/interrupt`, { method: 'POST' });

export const getAgentFiles = (token: string, chatId: string) =>
	request(token, `/chats/${chatId}/files`);

export const downloadAgentFile = async (token: string, chatId: string, file: any) => {
	const response = await fetch(
		`${WEBUI_API_BASE_URL}/agentapi/chats/${chatId}/files/${file.id}/content`,
		{ headers: { Authorization: `Bearer ${token}` } }
	);
	if (!response.ok) {
		const error = await response.json().catch(() => ({ detail: response.statusText }));
		throw error.detail ?? error;
	}
	const url = URL.createObjectURL(await response.blob());
	const anchor = document.createElement('a');
	anchor.href = url;
	anchor.download = file.filename ?? file.id;
	anchor.click();
	URL.revokeObjectURL(url);
};

export const toggleAgentChatArchive = (token: string, chatId: string) =>
	request(token, `/chats/${chatId}/archive`, { method: 'POST' });

export const deleteAgentChat = (token: string, chatId: string) =>
	request(token, `/chats/${chatId}`, { method: 'DELETE' });
