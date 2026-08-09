---
name: remembering-conversations
description: Legacy explicit-only compatibility entry for conversation recall. Use only when the user explicitly invokes $remembering-conversations; otherwise use episodic-memory:remembering-conversations, which owns historical conversation retrieval.
---

# Remembering Conversations Compatibility

Delegate the request to `episodic-memory:remembering-conversations`. Do not maintain a second
retrieval policy, copy raw conversations, or treat this compatibility entry as a separate memory
source.

Preserve the user's query unchanged when handing it off. Return the external Skill's evidence and
uncertainty without adding claims from this wrapper.
