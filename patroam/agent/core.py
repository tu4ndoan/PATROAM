"""Agent: the model-agnostic brain that the UI and the daemon both drive.

It owns the conversation history, the persona, the user's long-term memory, and
the action layer. It delegates generation to whatever Provider it was given, and
centrally handles two things so every frontend benefits for free:

  * Memory + tool instructions are injected into the system prompt each turn.
  * `ACTION:` directives the model emits are stripped from the streamed/spoken
    output and executed (open apps, remember facts, …).
"""

from .. import actions, config
from ..memory import get_memory

# Hold back this many trailing chars while streaming so a partial "ACTION:"
# marker can't leak into the spoken text before we recognise it.
_HOLD = 8
# Cap how many tool→result→answer rounds one request may take.
_MAX_TOOL_ROUNDS = 4


class Agent:
    def __init__(self, provider, model=None, system_prompt=None, memory=None):
        self.provider = provider
        self.model = model
        self.base_system = system_prompt or config.SYSTEM_PROMPT
        self.memory = memory if memory is not None else get_memory()
        self.history = []  # [{"role", "content"}]

    def set_model(self, model):
        self.model = model

    def reset(self):
        self.history.clear()

    def _system(self):
        # Persona + how to use tools + what we remember about the user.
        return f"{self.base_system}\n\n{actions.tools_prompt()}\n\n{self.memory.render()}"

    def _messages(self):
        return [{"role": "system", "content": self._system()}] + self.history

    def send(self, text, on_token, on_done, on_error):
        """Stream the reply, hide ACTION directives from the user, run them, and —
        if a tool returned data — feed it back and continue until the model has a
        final spoken answer."""
        self.history.append({"role": "user", "content": text})
        self._turn(on_token, on_done, on_error, 0)

    def _turn(self, on_token, on_done, on_error, rnd):
        state = {"raw": [], "sent": 0}

        def forward(s, final=False):
            idx = s.find("ACTION:")
            if idx >= 0:
                target = idx                       # never show the action block
            elif final:
                target = len(s)
            else:
                target = max(0, len(s) - _HOLD)    # hold a possible partial marker
            if target > state["sent"]:
                on_token(s[state["sent"]:target])
                state["sent"] = target

        def tok(t):
            state["raw"].append(t)
            forward("".join(state["raw"]))

        def done(full):
            forward(full, final=True)              # flush the held tail
            spoken, acts = actions.split(full)
            self.history.append({"role": "assistant", "content": spoken})

            results = []
            for name, args in acts:
                try:
                    r = actions.run(name, args)
                except Exception as e:
                    r = f"(error: {e})"
                if isinstance(r, str):             # a data tool returned something
                    results.append((name, r))

            if results and rnd < _MAX_TOOL_ROUNDS:
                summary = "\n".join(f"{n}: {res}" for n, res in results)
                self.history.append({"role": "user", "content": (
                    "Results of the tool(s) you just called — use these to answer my "
                    "question, naturally and concisely, without mentioning tools:\n"
                    + summary)})
                self._turn(on_token, on_done, on_error, rnd + 1)   # answer with the data
            else:
                on_done(spoken)

        self.provider.stream_chat(self.model, self._messages(), tok, done, on_error)
