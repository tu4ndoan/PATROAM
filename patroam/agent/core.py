"""Agent: the model-agnostic brain that the UI and the daemon both drive.

It owns the conversation history and the system persona, and delegates actual
generation to whatever Provider it was given.
"""

from .. import config


class Agent:
    def __init__(self, provider, model=None, system_prompt=None):
        self.provider = provider
        self.model = model
        self.system_prompt = system_prompt or config.SYSTEM_PROMPT
        self.history = []  # [{"role", "content"}]

    def set_model(self, model):
        self.model = model

    def reset(self):
        self.history.clear()

    def _messages(self):
        msgs = []
        if self.system_prompt:
            msgs.append({"role": "system", "content": self.system_prompt})
        msgs.extend(self.history)
        return msgs

    def send(self, text, on_token, on_done, on_error):
        """Append the user's message and stream the reply via the provider.

        The assistant's full reply is appended to history on completion.
        """
        self.history.append({"role": "user", "content": text})

        def done(full):
            self.history.append({"role": "assistant", "content": full})
            on_done(full)

        self.provider.stream_chat(self.model, self._messages(), on_token, done, on_error)
