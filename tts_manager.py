"""Text-to-speech manager for ZorkGPT.

This module provides a TTSManager class that handles text-to-speech synthesis
with support for multiple backends (pyttsx3, XTTS, edge-tts) and voice profiles.
"""

import asyncio
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional


class TTSBackend(ABC):
    """Abstract base class for TTS backends."""

    @abstractmethod
    def initialize(self) -> bool:
        """Initialize the backend. Returns True if successful."""
        pass

    @abstractmethod
    def speak(self, text: str, voice_type: str = "agent") -> None:
        """Speak the given text using the specified voice type."""
        pass

    @abstractmethod
    def get_available_voices(self) -> dict:
        """Get available voice profiles."""
        pass

    def cleanup(self) -> None:
        """Clean up resources (optional)."""
        pass


class Pyttsx3Backend(TTSBackend):
    """pyttsx3 TTS backend - fast, offline, cross-platform."""

    def __init__(self, voice_rate: int = 180, voice_volume: float = 0.8):
        self.voice_rate = voice_rate
        self.voice_volume = voice_volume
        self.engine = None
        self.voices = {}

    def initialize(self) -> bool:
        """Initialize pyttsx3 engine."""
        try:
            import pyttsx3
            self.engine = pyttsx3.init()

            # Configure voice settings
            self.engine.setProperty('rate', self.voice_rate)
            self.engine.setProperty('volume', self.voice_volume)

            # Get available voices
            voices = self.engine.getProperty('voices')
            if voices:
                self._setup_voice_profiles(voices)
                print(f"🎤 pyttsx3 initialized with {len(self.voices)} voice profiles")
                return True
            else:
                print("⚠️  No pyttsx3 voices available")
                return False

        except ImportError:
            print("⚠️  pyttsx3 not installed (pip install pyttsx3)")
            return False
        except Exception as e:
            print(f"⚠️  pyttsx3 initialization failed: {e}")
            return False

    def _setup_voice_profiles(self, voices):
        """Set up different voice profiles for different speakers."""
        # Agent voice (female, clear, analytical)
        agent_voice = None
        game_voice = None

        # Prioritize English voices and gender differentiation
        english_female_voices = []
        english_male_voices = []
        english_neutral_voices = []
        other_voices = []

        for voice in voices:
            name_lower = voice.name.lower() if hasattr(voice, 'name') else ""
            lang_lower = voice.languages[0].lower() if hasattr(voice, 'languages') and voice.languages else ""

            # Check if it's an English voice - be more permissive
            is_english = (lang_lower.startswith('en') or 'english' in name_lower or
                         lang_lower in ['en-us', 'en-gb', 'en-029', 'en-gb-scotland',
                                       'en-gb-x-gbclan', 'en-gb-x-gbcwmd', 'en-gb-x-rp',
                                       'en-us-nyc'])

            if is_english:
                # English voice - check gender
                if hasattr(voice, 'gender') and voice.gender:
                    if 'female' in voice.gender.lower():
                        english_female_voices.append(voice)
                    elif 'male' in voice.gender.lower():
                        english_male_voices.append(voice)
                    else:
                        english_neutral_voices.append(voice)
                else:
                    # Fallback: check voice name for gender clues
                    if any(word in name_lower for word in ['female', 'woman', 'girl', 'karen', 'samantha', 'zira']):
                        english_female_voices.append(voice)
                    elif any(word in name_lower for word in ['male', 'man', 'boy', 'david', 'mark', 'paul']):
                        english_male_voices.append(voice)
                    else:
                        english_neutral_voices.append(voice)
            else:
                other_voices.append(voice)

        # Assign agent voice (prefer proper English voices, avoid pseudo-English like Mandarin)
        proper_english_voices = []
        pseudo_english_voices = []

        for voice in english_male_voices + english_female_voices + english_neutral_voices:
            name_lower = voice.name.lower() if hasattr(voice, 'name') else ""
            lang_lower = voice.languages[0].lower() if hasattr(voice, 'languages') and voice.languages else ""

            # Consider it pseudo-English if it has non-English words in name or complex language codes
            if ('mandarin' in name_lower or 'latin' in name_lower or 'pinyin' in name_lower or
                'cmn' in lang_lower or '-' in lang_lower and not lang_lower.startswith('en-')):
                pseudo_english_voices.append(voice)
            else:
                proper_english_voices.append(voice)

        # Assign agent voice (prefer proper English male/female, then pseudo-English, then any)
        if proper_english_voices:
            # Prefer female if available, otherwise first proper English voice
            female_proper = [v for v in proper_english_voices if hasattr(v, 'gender') and v.gender and 'female' in v.gender.lower()]
            if female_proper:
                agent_voice = female_proper[0]
            else:
                agent_voice = proper_english_voices[0]
        elif pseudo_english_voices:
            agent_voice = pseudo_english_voices[0]
        elif voices:
            agent_voice = voices[0]  # Fallback to first available

        # Assign game voice (prefer different proper English voice from agent)
        available_game_voices = [v for v in proper_english_voices if v != agent_voice]

        if available_game_voices:
            # Prefer male voice for game if available
            male_proper = [v for v in available_game_voices if hasattr(v, 'gender') and v.gender and 'male' in v.gender.lower()]
            if male_proper:
                game_voice = male_proper[0]
            else:
                game_voice = available_game_voices[0]
        elif pseudo_english_voices and agent_voice not in pseudo_english_voices:
            game_voice = [v for v in pseudo_english_voices if v != agent_voice][0] if len([v for v in pseudo_english_voices if v != agent_voice]) > 0 else pseudo_english_voices[0]
        elif len(voices) > 1:
            # Find any different voice
            for voice in voices:
                if voice != agent_voice:
                    game_voice = voice
                    break
            if not game_voice:
                game_voice = voices[0]  # Fallback
        else:
            # Only one voice available
            game_voice = agent_voice

        self.voices = {
            'agent': agent_voice,
            'game': game_voice
        }

        agent_info = agent_voice.name if hasattr(agent_voice, 'name') else f'Voice {voices.index(agent_voice)}'
        game_info = game_voice.name if hasattr(game_voice, 'name') else f'Voice {voices.index(game_voice)}'

        if hasattr(agent_voice, 'languages') and agent_voice.languages:
            agent_info += f' ({agent_voice.languages[0]})'
        if hasattr(game_voice, 'languages') and game_voice.languages:
            game_info += f' ({game_voice.languages[0]})'

        print(f"   🤖 Agent voice: {agent_info}")
        print(f"   🎮 Game voice: {game_info}")

    def speak(self, text: str, voice_type: str = "agent") -> None:
        """Speak the given text using the specified voice."""
        if not self.engine or voice_type not in self.voices:
            return

        try:
            # Set the appropriate voice
            voice = self.voices[voice_type]
            self.engine.setProperty('voice', voice.id)

            # Clean up the text for better speech synthesis
            clean_text = self._clean_text_for_speech(text)

            if clean_text:
                self.engine.say(clean_text)
                self.engine.runAndWait()

        except Exception as e:
            print(f"⚠️  pyttsx3 speak failed: {e}")

    def get_available_voices(self) -> dict:
        """Get available voice profiles."""
        return self.voices

    def _clean_text_for_speech(self, text: str) -> str:
        """Clean text for better speech synthesis."""
        # Remove emojis and special characters that might cause issues
        text = re.sub(r'[^\w\s.,!?-]', '', text)
        # Replace multiple spaces with single space
        text = re.sub(r'\s+', ' ', text)
        return text.strip()


class XTTSBackend(TTSBackend):
    """Coqui XTTS v2 backend - high quality, voice cloning, multilingual."""

    def __init__(self, model_name: str = "tts_models/multilingual/multi-dataset/xtts_v2",
                 speaker_wavs: Optional[dict] = None, language: str = "en"):
        """
        Initialize XTTS backend.

        Args:
            model_name: XTTS model name
            speaker_wavs: Dictionary mapping voice types to reference audio files
                         e.g., {"agent": "agent_voice.wav", "game": "narrator_voice.wav"}
            language: Language code (en, es, fr, de, it, pt, pl, tr, ru, nl, cs, ar, zh-cn, ja)
        """
        self.model_name = model_name
        self.speaker_wavs = speaker_wavs or {}
        self.language = language
        self.tts = None
        self.voices = {}

    def initialize(self) -> bool:
        """Initialize XTTS engine."""
        try:
            from TTS.api import TTS
            
            print(f"🎤 Loading XTTS v2 model (this may take a moment)...")
            self.tts = TTS(self.model_name)
            
            # Set up voice profiles
            if self.speaker_wavs:
                for voice_type, wav_path in self.speaker_wavs.items():
                    if Path(wav_path).exists():
                        self.voices[voice_type] = wav_path
                        print(f"   🎤 {voice_type.capitalize()} voice: {wav_path}")
                    else:
                        print(f"⚠️  Speaker wav not found: {wav_path}")
            
            if not self.voices:
                print("⚠️  No valid speaker wavs provided for XTTS")
                print("   💡 XTTS requires reference audio files for voice cloning")
                print("   💡 Provide speaker_wavs={'agent': 'voice.wav', 'game': 'narrator.wav'}")
                return False
                
            print(f"🎤 XTTS initialized with {len(self.voices)} voice profiles")
            return True

        except ImportError:
            print("⚠️  XTTS not installed (pip install TTS)")
            return False
        except Exception as e:
            print(f"⚠️  XTTS initialization failed: {e}")
            return False

    def speak(self, text: str, voice_type: str = "agent") -> None:
        """Speak the given text using XTTS."""
        if not self.tts or voice_type not in self.voices:
            if voice_type not in self.voices:
                print(f"⚠️  Voice type '{voice_type}' not available in XTTS")
            return

        try:
            import tempfile
            import subprocess
            
            # Clean text
            clean_text = self._clean_text_for_speech(text)
            if not clean_text:
                return

            # Generate speech to temporary file
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp_file:
                tmp_path = tmp_file.name
                
            self.tts.tts_to_file(
                text=clean_text,
                speaker_wav=self.voices[voice_type],
                language=self.language,
                file_path=tmp_path
            )
            
            # Play the audio file
            try:
                # Try different audio players
                for cmd in [['aplay', tmp_path], ['paplay', tmp_path], 
                           ['ffplay', '-nodisp', '-autoexit', tmp_path]]:
                    try:
                        subprocess.run(cmd, check=True, capture_output=True)
                        break
                    except (subprocess.CalledProcessError, FileNotFoundError):
                        continue
            finally:
                # Clean up temp file
                try:
                    Path(tmp_path).unlink()
                except Exception:
                    pass

        except Exception as e:
            print(f"⚠️  XTTS speak failed: {e}")

    def get_available_voices(self) -> dict:
        """Get available voice profiles."""
        return self.voices

    def _clean_text_for_speech(self, text: str) -> str:
        """Clean text for better speech synthesis."""
        # Remove emojis and special characters
        text = re.sub(r'[^\w\s.,!?-]', '', text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip()


class EdgeTTSBackend(TTSBackend):
    """Microsoft Edge TTS backend - high quality, free, cloud-based."""

    def __init__(self, voices: Optional[dict] = None):
        """
        Initialize Edge TTS backend.

        Args:
            voices: Dictionary mapping voice types to Edge voice names
                   e.g., {"agent": "en-US-JennyNeural", "game": "en-US-GuyNeural"}
                   
        Popular English voices:
            - en-US-JennyNeural (female, friendly)
            - en-US-GuyNeural (male, warm)
            - en-US-AriaNeural (female, conversational)
            - en-US-DavisNeural (male, strong)
            - en-GB-SoniaNeural (female, British)
            - en-GB-RyanNeural (male, British)
        """
        self.voice_names = voices or {
            'agent': 'en-US-JennyNeural',
            'game': 'en-US-GuyNeural'
        }
        self.voices = {}

    def initialize(self) -> bool:
        """Initialize Edge TTS."""
        try:
            import edge_tts
            
            # Verify voices are valid
            self.voices = self.voice_names.copy()
            
            print(f"🎤 Edge TTS initialized with {len(self.voices)} voice profiles")
            for voice_type, voice_name in self.voices.items():
                print(f"   🎤 {voice_type.capitalize()} voice: {voice_name}")
            
            return True

        except ImportError:
            print("⚠️  edge-tts not installed (pip install edge-tts)")
            return False
        except Exception as e:
            print(f"⚠️  Edge TTS initialization failed: {e}")
            return False

    def speak(self, text: str, voice_type: str = "agent") -> None:
        """Speak the given text using Edge TTS."""
        if voice_type not in self.voices:
            print(f"⚠️  Voice type '{voice_type}' not available in Edge TTS")
            return

        try:
            import edge_tts
            import tempfile
            import subprocess
            
            # Clean text
            clean_text = self._clean_text_for_speech(text)
            if not clean_text:
                return

            # Generate speech asynchronously
            async def _generate_and_play():
                with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as tmp_file:
                    tmp_path = tmp_file.name
                
                try:
                    # Generate speech
                    communicate = edge_tts.Communicate(clean_text, self.voices[voice_type])
                    await communicate.save(tmp_path)
                    
                    # Play the audio file
                    for cmd in [['mpg123', '-q', tmp_path], ['ffplay', '-nodisp', '-autoexit', tmp_path],
                               ['paplay', tmp_path], ['aplay', tmp_path]]:
                        try:
                            subprocess.run(cmd, check=True, capture_output=True)
                            break
                        except (subprocess.CalledProcessError, FileNotFoundError):
                            continue
                finally:
                    # Clean up temp file
                    try:
                        Path(tmp_path).unlink()
                    except Exception:
                        pass

            # Run the async function
            asyncio.run(_generate_and_play())

        except Exception as e:
            print(f"⚠️  Edge TTS speak failed: {e}")

    def get_available_voices(self) -> dict:
        """Get available voice profiles."""
        return self.voices

    def _clean_text_for_speech(self, text: str) -> str:
        """Clean text for better speech synthesis."""
        # Remove emojis and special characters
        text = re.sub(r'[^\w\s.,!?-]', '', text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip()


class TTSManager:
    """Main TTS manager that orchestrates multiple backends."""

    def __init__(self, enabled: bool = True, backend: str = "pyttsx3", quality: str = "medium",
                 voice_rate: int = 180, voice_volume: float = 0.8, **backend_kwargs):
        """
        Initialize TTS manager with specified backend.

        Args:
            enabled: Whether TTS is enabled
            backend: Backend to use ('pyttsx3', 'xtts', 'edge')
            quality: Quality setting ('low', 'medium', 'high')
            voice_rate: Speech rate for pyttsx3 (words per minute)
            voice_volume: Speech volume for pyttsx3 (0.0 to 1.0)
            **backend_kwargs: Additional backend-specific arguments
                - For XTTS: speaker_wavs, language
                - For Edge TTS: voices
        """
        self.enabled = enabled
        self.backend_name = backend
        self.quality = quality
        self.backend: Optional[TTSBackend] = None

        if not self.enabled:
            print("🔇 TTS disabled")
            return

        # Initialize the requested backend
        if backend == "pyttsx3":
            self.backend = Pyttsx3Backend(voice_rate=voice_rate, voice_volume=voice_volume)
        elif backend == "xtts":
            self.backend = XTTSBackend(
                speaker_wavs=backend_kwargs.get('speaker_wavs'),
                language=backend_kwargs.get('language', 'en')
            )
        elif backend == "edge":
            self.backend = EdgeTTSBackend(voices=backend_kwargs.get('voices'))
        else:
            print(f"⚠️  Unknown backend '{backend}' - trying fallbacks")
            self.backend = None

        # Try to initialize
        if self.backend and not self.backend.initialize():
            print(f"⚠️  {backend} backend failed - trying fallback")
            self.backend = None

        # Fallback to pyttsx3 if primary backend fails
        if self.backend is None and backend != "pyttsx3":
            print("   Falling back to pyttsx3...")
            self.backend = Pyttsx3Backend(voice_rate=voice_rate, voice_volume=voice_volume)
            if not self.backend.initialize():
                print("⚠️  All TTS backends failed - TTS disabled")
                self.enabled = False
                self.backend = None

    def speak(self, text: str, prefix: str = "", voice_type: str = "agent") -> None:
        """
        Speak the given text using the configured backend.

        Args:
            text: Text to speak
            prefix: Optional prefix to add to the spoken text
            voice_type: Type of voice to use ('agent' or 'game')
        """
        if not self.enabled or not self.backend:
            return

        # Add prefix if provided
        full_text = f"{prefix} {text}".strip() if prefix else text
        
        try:
            self.backend.speak(full_text, voice_type=voice_type)
        except Exception as e:
            print(f"⚠️  TTS speak failed: {e}")

    def get_backend_name(self) -> str:
        """Get the name of the active backend."""
        if not self.enabled or not self.backend:
            return "none"
        return self.backend_name

    def get_available_voices(self) -> dict:
        """Get available voice profiles from the backend."""
        if not self.enabled or not self.backend:
            return {}
        return self.backend.get_available_voices()

    def cleanup(self) -> None:
        """Clean up backend resources."""
        if self.backend:
            self.backend.cleanup()

