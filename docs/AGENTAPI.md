# Qiniu AgentAPI

Open WebUI can run a separate class of persistent conversations through the native [Qiniu AgentAPI](https://agentapi-user-docs.vercel.app/) session and event protocol.

## Configure

1. Bind a MaaS API key in the Qiniu Portal.
2. Open **Admin Settings → AgentAPI**.
3. Enter the regional endpoint (`https://agent.qiniuapi.com` for China or `https://agent.sufy.com` for overseas), the key, and one or more Agent profiles.
4. Each profile binds an Agent ID and version to an Environment ID. Use its access control dialog to grant access to all users, groups, or individual users.
5. Enable **Agent Mode** in the relevant group permissions. Administrators always have access.

The same values can be bootstrapped with `AGENTAPI_ENABLED`, `AGENTAPI_BASE_URL`, `AGENTAPI_API_KEY`, and `AGENTAPI_PROFILES` environment variables.

## Conversation behavior

Agent conversations are stored with `mode=agent`, appear in a separate **Agents** section, and always continue through AgentAPI. They cannot be sent to the OpenAI-compatible chat completion endpoint, shared, cloned into a normal chat, or switched to Chat mode.

User and Agent messages are persisted in the normal Open WebUI chat tables. Consequently, existing administrator chat access and employee-conversation audit flows include Agent conversations without a separate audit store. The remote AgentAPI Session ID and immutable profile snapshot are stored in server-managed chat metadata; the MaaS API key is never returned from user-facing APIs.
