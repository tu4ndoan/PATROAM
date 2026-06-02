"""Push-to-talk recorder: capture audio while a button is held, then transcribe.

Used by the GUI's "Hold to Talk" button. The always-on path uses
`listener.WakeWordListener` instead.
"""

import wave

import pyaudio
import speech_recognition as sr

from .. import config


class VoiceRecorder:
    def __init__(self):
        self.pa = pyaudio.PyAudio()
        self.stream = None
        self.frames = []
        self.recording = False
        self.CHUNK = 1024
        self.FORMAT = pyaudio.paInt16
        self.CHANNELS = 1
        self.RATE = 16000

    def start(self):
        self.frames = []
        self.recording = True
        self.stream = self.pa.open(
            format=self.FORMAT, channels=self.CHANNELS,
            rate=self.RATE, input=True, frames_per_buffer=self.CHUNK,
        )

        def record():
            while self.recording:
                data = self.stream.read(self.CHUNK, exception_on_overflow=False)
                self.frames.append(data)

        import threading
        self._thread = threading.Thread(target=record, daemon=True)
        self._thread.start()

    def stop(self):
        self.recording = False
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
        return self.frames, self.RATE, self.FORMAT, self.CHANNELS

    def transcribe(self):
        """Save the recorded audio to a temp WAV and transcribe it."""
        frames, rate, fmt, ch = self.stop()
        tmp = config.VOICE_TMP_WAV
        wf = wave.open(tmp, "wb")
        wf.setnchannels(ch)
        wf.setsampwidth(self.pa.get_sample_size(fmt))
        wf.setframerate(rate)
        wf.writeframes(b"".join(frames))
        wf.close()

        r = sr.Recognizer()
        with sr.AudioFile(tmp) as src:
            audio = r.record(src)
        try:
            return r.recognize_google(audio)
        except sr.UnknownValueError:
            return ""
        except sr.RequestError as e:
            return f"[Speech API error: {e}]"
