"""Tests del módulo de voz (magnus/voice/).

Ninguno necesita tarjeta de sonido ni motor de voz instalado: la narración, las
frases y los comentarios son lógica pura, y el nodo se prueba con
``FakeSpeechBackend``, que solo registra lo que se habría dicho.
"""

import random
import time

import pytest

from magnus import config
from magnus.core.messages import MoveResponse
from magnus.voice import (
    Advantage,
    Commentator,
    FakeSpeechBackend,
    MoveQuality,
    PhrasePicker,
    PositionEval,
    VoiceNode,
    classify_advantage,
    classify_delta,
    describe_move,
    piece_name,
    square_to_speech,
    to_robot_pov,
)
from magnus.voice.speech_text import SpeechTextError


def wait_until_spoken(backend: FakeSpeechBackend, count: int, timeout: float = 2.0):
    """Espera a que el hilo de voz haya dicho ``count`` frases."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if len(backend.spoken) >= count:
            return
        time.sleep(0.01)
    raise AssertionError(f"Solo se dijeron {len(backend.spoken)} de {count} frases.")


# --------------------------------------------------------------------------- #
# Texto hablable
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "square,expected",
    [("g1", "ge uno"), ("a8", "a ocho"), ("f3", "efe tres"), ("h7", "hache siete")],
)
def test_square_to_speech(square, expected):
    assert square_to_speech(square) == expected


def test_square_to_speech_is_case_insensitive():
    assert square_to_speech("E4") == square_to_speech("e4")


@pytest.mark.parametrize("bad", ["", "z9", "e", "e0", "e9", "11"])
def test_invalid_square_raises(bad):
    with pytest.raises(SpeechTextError):
        square_to_speech(bad)


def test_piece_names_ignore_colour():
    """El nombre de la pieza es el mismo para blancas y negras."""
    assert piece_name("N") == piece_name("n") == "caballo"
    assert piece_name("q") == "dama"


def test_describe_simple_move():
    resp = MoveResponse(from_square="g1", to_square="f3", piece="N")
    assert describe_move(resp) == "Muevo el caballo de ge uno a efe tres."


def test_describe_move_from_the_human_side():
    resp = MoveResponse(from_square="e2", to_square="e4", piece="P")
    assert describe_move(resp, speaker="human") == "Moviste el peón de e dos a e cuatro."


def test_describe_capture_with_check():
    resp = MoveResponse(from_square="f3", to_square="e5", piece="N",
                        is_capture=True, captured_piece="p", is_check=True)
    frase = describe_move(resp)
    assert "Capturo el peón en e cinco" in frase and frase.endswith("Jaque.")


def test_describe_en_passant_mentions_it():
    resp = MoveResponse(from_square="d5", to_square="c6", piece="P", is_capture=True,
                        captured_piece="p", captured_square="c5", is_en_passant=True)
    assert "al paso" in describe_move(resp)


def test_describe_castling_is_named_not_spelled():
    corto = MoveResponse(from_square="e1", to_square="g1", piece="K",
                         is_castling=True, is_kingside_castle=True)
    largo = MoveResponse(from_square="e1", to_square="c1", piece="K", is_castling=True)
    assert describe_move(corto) == "Enroco corto."
    assert describe_move(largo) == "Enroco largo."


def test_describe_promotion():
    resp = MoveResponse(from_square="a7", to_square="a8", piece="P", promotion="q")
    frase = describe_move(resp)
    assert "corona" in frase and "la dama" in frase


def test_checkmate_suffix_wins_over_check():
    resp = MoveResponse(from_square="d1", to_square="h5", piece="Q",
                        is_check=True, is_checkmate=True)
    assert describe_move(resp).endswith("Jaque mate.")


@pytest.mark.parametrize(
    "from_sq,to_sq,forbidden",
    [("d1", "h5", " de de "), ("b1", "a3", " a a ")],
)
def test_no_stuttering_prepositions(from_sq, to_sq, forbidden):
    """"de de cinco" o "a a ocho" suenan a tartamudeo: se usan desde/hasta."""
    resp = MoveResponse(from_square=from_sq, to_square=to_sq, piece="N")
    assert forbidden not in describe_move(resp)


def test_unknown_speaker_raises():
    with pytest.raises(SpeechTextError):
        describe_move(MoveResponse(from_square="e2", to_square="e4"), speaker="gato")


# --------------------------------------------------------------------------- #
# Evaluaciones y clasificación
# --------------------------------------------------------------------------- #
def test_eval_is_flipped_when_the_robot_is_not_to_move():
    """La eval viene del lado que mueve; hay que pasarla al punto de vista del robot."""
    posicion = PositionEval(evaluation_cp=150, side_to_move="white")
    assert to_robot_pov(posicion, robot_side="white") == 150
    assert to_robot_pov(posicion, robot_side="black") == -150


def test_mate_beats_any_centipawn_score():
    mate = PositionEval(mate_in=3, side_to_move="black")
    assert to_robot_pov(mate, "black") > 5000
    assert to_robot_pov(PositionEval(mate_in=-2, side_to_move="black"), "black") < -5000


def test_closer_mate_scores_higher():
    cerca = to_robot_pov(PositionEval(mate_in=1, side_to_move="white"), "white")
    lejos = to_robot_pov(PositionEval(mate_in=8, side_to_move="white"), "white")
    assert cerca > lejos


def test_position_without_evaluation_is_none():
    assert to_robot_pov(PositionEval(), "white") is None


@pytest.mark.parametrize(
    "delta,expected",
    [
        (900, MoveQuality.BLUNDER),
        (300, MoveQuality.BLUNDER),
        (200, MoveQuality.MISTAKE),
        (70, MoveQuality.INACCURACY),
        (10, MoveQuality.NEUTRAL),
        (-10, MoveQuality.NEUTRAL),
        (-80, MoveQuality.GOOD),
        (-400, MoveQuality.GREAT),
    ],
)
def test_classify_delta(delta, expected):
    assert classify_delta(delta) is expected


def test_neutral_moves_are_not_worth_saying():
    assert not MoveQuality.NEUTRAL.is_worth_saying
    assert MoveQuality.BLUNDER.is_worth_saying


@pytest.mark.parametrize(
    "cp,expected",
    [(500, Advantage.ROBOT), (0, Advantage.EQUAL), (-500, Advantage.HUMAN)],
)
def test_classify_advantage(cp, expected):
    assert classify_advantage(cp) is expected


# --------------------------------------------------------------------------- #
# Commentator
# --------------------------------------------------------------------------- #
def test_first_move_has_nothing_to_compare_with():
    com = Commentator(robot_side="black")
    assert com.comment_human_move(PositionEval(evaluation_cp=20, side_to_move="black")) is None


def test_blunder_by_the_human_is_commented():
    com = Commentator(robot_side="black")
    com.observe(PositionEval(evaluation_cp=20, side_to_move="black"))
    frase = com.comment_human_move(PositionEval(evaluation_cp=900, side_to_move="black"))
    assert frase is not None


def test_good_human_move_is_praised_not_scolded():
    com = Commentator(robot_side="black")
    com.observe(PositionEval(evaluation_cp=200, side_to_move="black"))
    com.judge_human_move(PositionEval(evaluation_cp=-100, side_to_move="black"))
    com2 = Commentator(robot_side="black")
    com2.observe(PositionEval(evaluation_cp=200, side_to_move="black"))
    assert com2.judge_human_move(
        PositionEval(evaluation_cp=-100, side_to_move="black")
    ) is MoveQuality.GREAT


def test_ordinary_move_gets_no_comment():
    com = Commentator(robot_side="black")
    com.observe(PositionEval(evaluation_cp=20, side_to_move="black"))
    assert com.comment_human_move(PositionEval(evaluation_cp=30, side_to_move="black")) is None


def test_advantage_is_not_repeated_every_move():
    """Decir "voy ganando" en cada jugada es insoportable."""
    com = Commentator(robot_side="black", advantage_every=0)
    ganando = PositionEval(evaluation_cp=500, side_to_move="black")
    assert com.comment_advantage(ganando) is not None      # cambió: se dice
    assert com.comment_advantage(ganando) is None          # sigue igual: se calla


def test_advantage_speaks_again_when_it_flips():
    com = Commentator(robot_side="black", advantage_every=0)
    com.comment_advantage(PositionEval(evaluation_cp=500, side_to_move="black"))
    frase = com.comment_advantage(PositionEval(evaluation_cp=-500, side_to_move="black"))
    assert frase is not None


def test_reset_forgets_previous_evaluation():
    com = Commentator(robot_side="black")
    com.observe(PositionEval(evaluation_cp=20, side_to_move="black"))
    com.reset()
    assert com.comment_human_move(PositionEval(evaluation_cp=900, side_to_move="black")) is None


def test_invalid_robot_side_raises():
    with pytest.raises(ValueError):
        Commentator(robot_side="verde")


# --------------------------------------------------------------------------- #
# Frases
# --------------------------------------------------------------------------- #
def test_picker_avoids_repeating_the_same_phrase():
    picker = PhrasePicker(rng=random.Random(0))
    opciones = ("uno", "dos", "tres")
    dichas = [picker.pick(opciones) for _ in range(6)]
    assert all(a != b for a, b in zip(dichas, dichas[1:]))


def test_picker_survives_a_single_option():
    picker = PhrasePicker(rng=random.Random(0))
    assert picker.pick(("única",)) == "única"
    assert picker.pick(("única",)) == "única"


def test_picker_rejects_empty_group():
    with pytest.raises(ValueError):
        PhrasePicker().pick(())


# --------------------------------------------------------------------------- #
# VoiceNode
# --------------------------------------------------------------------------- #
def test_voice_node_speaks_in_the_background():
    backend = FakeSpeechBackend()
    with VoiceNode(backend=backend) as voz:
        voz.announce_move(MoveResponse(from_square="g1", to_square="f3", piece="N"))
        wait_until_spoken(backend, 1)
    assert backend.spoken == ["Muevo el caballo de ge uno a efe tres."]


def test_muted_node_keeps_the_subtitle_but_stays_silent():
    backend = FakeSpeechBackend()
    with VoiceNode(backend=backend, muted=True) as voz:
        voz.say("hola")
        time.sleep(0.1)
        assert voz.last_phrase == "hola"        # el subtítulo sigue apareciendo
    assert backend.spoken == []                 # pero no sonó nada


def test_toggle_mute():
    with VoiceNode(backend=FakeSpeechBackend()) as voz:
        assert voz.toggle_mute() is True
        assert voz.toggle_mute() is False


def test_queue_drops_the_oldest_when_overloaded():
    """Comentar una jugada de hace tres turnos es peor que callarse."""
    voz = VoiceNode(backend=FakeSpeechBackend(), queue_max=2)
    for i in range(6):                          # sin arrancar el hilo consumidor
        voz.say(f"frase {i}")
    assert voz._queue.qsize() <= 2
    assert voz.last_phrase == "frase 5"         # la más reciente sobrevive


def test_say_now_clears_what_was_pending():
    voz = VoiceNode(backend=FakeSpeechBackend(), queue_max=3)
    voz.say("vieja")
    voz.say_now("urgente")
    assert voz._queue.qsize() == 1
    assert voz.last_phrase == "urgente"


def test_empty_text_is_ignored():
    backend = FakeSpeechBackend()
    with VoiceNode(backend=backend) as voz:
        voz.say(None)
        voz.say("")
        time.sleep(0.1)
    assert backend.spoken == []


def test_a_broken_backend_never_stops_the_game():
    """Si la voz peta, la partida sigue: el robot se queda mudo y ya."""

    class BrokenBackend(FakeSpeechBackend):
        def speak(self, text):
            raise RuntimeError("tarjeta de sonido en llamas")

    with VoiceNode(backend=BrokenBackend()) as voz:
        voz.say("esto va a fallar")
        time.sleep(0.1)
        voz.say("y esto también")
        time.sleep(0.1)
        assert voz.last_phrase == "y esto también"   # el nodo sigue vivo


def test_unnarratable_move_does_not_raise():
    """Una jugada incompleta no puede tumbar la partida por culpa de la voz."""
    backend = FakeSpeechBackend()
    with VoiceNode(backend=backend) as voz:
        voz.announce_move(MoveResponse())        # sin casillas
        time.sleep(0.1)
    assert backend.spoken == []


def test_new_game_resets_the_commentary_state():
    voz = VoiceNode(backend=FakeSpeechBackend())
    voz.commentator.observe(PositionEval(evaluation_cp=20, side_to_move="black"))
    voz.new_game()
    assert voz.comment_human_move(
        PositionEval(evaluation_cp=900, side_to_move="black")
    ) is None


def test_game_end_announcement():
    backend = FakeSpeechBackend()
    with VoiceNode(backend=backend) as voz:
        voz.announce_game_end(is_checkmate=True, winner_is_robot=False)
        wait_until_spoken(backend, 1)
    assert "anaste" in backend.spoken[0] or "ganaste" in backend.spoken[0].lower()


# --------------------------------------------------------------------------- #
# Configuración
# --------------------------------------------------------------------------- #
def test_comment_thresholds_are_ordered():
    """Los umbrales tienen que ir de menos a más grave, o la clasificación miente."""
    assert (config.COMMENT_INACCURACY_CP < config.COMMENT_MISTAKE_CP
            < config.COMMENT_BLUNDER_CP)
    assert config.COMMENT_GOOD_CP < config.COMMENT_GREAT_CP


# --------------------------------------------------------------------------- #
# Regresión: signo de los mates
# --------------------------------------------------------------------------- #
def test_being_checkmated_is_not_an_advantage():
    """``mate_in == 0`` significa "ya me han dado mate", no "gano".

    Con el signo mal, el robot cantaba "eso fue un error grave" en la jugada en
    la que acababan de darle jaque mate a él.
    """
    mateado = PositionEval(mate_in=0, side_to_move="black")
    assert to_robot_pov(mateado, robot_side="black") < 0
    assert to_robot_pov(mateado, robot_side="white") > 0


def test_scholars_mate_is_not_called_a_blunder():
    """El mate del pastor del rival es una gran jugada suya, no un error."""
    com = Commentator(robot_side="black")
    com.observe(PositionEval(evaluation_cp=40, side_to_move="black"))
    quality = com.judge_human_move(PositionEval(mate_in=0, side_to_move="black"))
    assert quality is MoveQuality.GREAT


# --------------------------------------------------------------------------- #
# Voces del sistema (macOS `say`)
# --------------------------------------------------------------------------- #
SAY_OUTPUT = """\
Alex                en_US    # Most people recognize me by my voice.
Diego               es_AR    # Hola, me llamo Diego.
Eddy (Spanish (Mexico)) es_MX # ¡Hola! Me llamo Eddy.
Jorge               es_ES    # Hola, me llamo Jorge.
Juan                es_MX    # Hola, me llamo Juan.
Paulina             es_MX    # Hola, me llamo Paulina.
Samantha            en_US    # Hello, my name is Samantha.
"""


def fake_say_listing(monkeypatch, output=SAY_OUTPUT, available=True):
    """Simula `say -v '?'` sin estar en macOS."""
    import subprocess

    from magnus.voice import backend as backend_module

    monkeypatch.setattr(backend_module.shutil, "which",
                        lambda name: "/usr/bin/say" if available else None)
    monkeypatch.setattr(
        backend_module.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=output, stderr=""),
    )


def test_list_system_voices_parses_names_with_spaces(monkeypatch):
    """Las voces modernas traen paréntesis: "Eddy (Spanish (Mexico))"."""
    from magnus.voice.backend import list_system_voices

    fake_say_listing(monkeypatch)
    voices = {v.name: v.locale for v in list_system_voices()}
    assert voices["Eddy (Spanish (Mexico))"] == "es_MX"
    assert voices["Juan"] == "es_MX"
    assert len(voices) == 7


def test_list_system_voices_can_filter_spanish(monkeypatch):
    from magnus.voice.backend import list_system_voices

    fake_say_listing(monkeypatch)
    nombres = [v.name for v in list_system_voices(spanish_only=True)]
    assert "Alex" not in nombres and "Juan" in nombres


def test_list_system_voices_is_empty_outside_macos(monkeypatch):
    from magnus.voice.backend import list_system_voices

    fake_say_listing(monkeypatch, available=False)
    assert list_system_voices() == []


def test_missing_voice_falls_back_to_a_spanish_male_one(monkeypatch):
    """Un nombre mal escrito no puede dejar mudo al robot en plena feria."""
    from magnus.voice.backend import MALE_SPANISH_VOICES, MacSayBackend

    fake_say_listing(monkeypatch)
    backend = MacSayBackend(voice="NoExisteEstaVoz")
    assert backend.voice in MALE_SPANISH_VOICES


def test_installed_voice_is_respected(monkeypatch):
    from magnus.voice.backend import MacSayBackend

    fake_say_listing(monkeypatch)
    assert MacSayBackend(voice="Paulina").voice == "Paulina"


def test_fallback_without_male_voices_uses_any_spanish(monkeypatch):
    from magnus.voice.backend import MacSayBackend

    fake_say_listing(monkeypatch, output="Paulina es_MX # Hola.\nAlex en_US # Hi.\n")
    assert MacSayBackend(voice="Juan").voice == "Paulina"


def test_default_macos_voice_is_male():
    """MAGNUS habla en masculino: la voz por defecto debe serlo."""
    from magnus.voice.backend import MALE_SPANISH_VOICES

    assert config.VOICE_MACOS_VOICE in MALE_SPANISH_VOICES


# --------------------------------------------------------------------------- #
# Piper: sin efectos de audio y elección de calidad
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "name,quality",
    [
        ("es_MX-claude-high", "high"),
        ("es_ES-davefx-medium", "medium"),
        ("es_ES-carlfm-x_low", "x_low"),
        ("voz_rara", "desconocida"),
    ],
)
def test_model_quality_from_name(name, quality):
    from magnus.voice.backend import model_quality

    assert model_quality(name) == quality


def test_default_piper_voice_is_high_quality():
    """Los modelos `medium`/`low` suenan apagados, "como debajo del agua"."""
    from magnus.voice.backend import model_quality

    assert model_quality(config.VOICE_PIPER_MODEL) == "high"


def test_default_speed_is_the_models_natural_one():
    """1.0 = cadencia natural del modelo; alejarse le quita naturalidad."""
    assert config.VOICE_LENGTH_SCALE == 1.0


def test_synthesis_applies_no_audio_effects(tmp_path, monkeypatch):
    """La voz sale tal cual del modelo: sin filtros, tono ni resampleo.

    Solo se pasa `length_scale`; el resto de parámetros quedan en los del
    modelo, y es Piper quien escribe la cabecera del WAV (escribirla a mano con
    otra frecuencia es justo lo que produce el efecto "bajo el agua").
    """
    import sys
    import types

    recorded = {}

    class FakePiperVoice:
        @staticmethod
        def load(path, *a, **k):
            return FakePiperVoice()

        def synthesize_wav(self, text, wav_file, syn_config=None, **kwargs):
            recorded["text"] = text
            recorded["config"] = syn_config
            recorded["kwargs"] = kwargs
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(22050)
            wav_file.writeframes(b"\x00\x00" * 100)

    class FakeSynthesisConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    fake_piper = types.ModuleType("piper")
    fake_piper.PiperVoice = FakePiperVoice
    fake_piper.SynthesisConfig = FakeSynthesisConfig
    monkeypatch.setitem(sys.modules, "piper", fake_piper)

    from magnus.voice.backend import PiperBackend

    modelo = tmp_path / "voz.onnx"
    modelo.write_bytes(b"no es un modelo de verdad")
    backend = PiperBackend(model=str(modelo))
    salida = backend.synthesize_to_file("hola", tmp_path / "salida.wav")

    assert salida.is_file()
    # Solo se toca la velocidad, y con el valor por defecto ni eso.
    assert vars(recorded["config"]) == {"length_scale": config.VOICE_LENGTH_SCALE}
    # No se fuerza el formato del WAV: lo escribe Piper con SU frecuencia.
    assert "set_wav_format" not in recorded["kwargs"]


def test_falls_back_to_any_downloaded_voice(tmp_path, monkeypatch):
    """Mejor una voz distinta a la configurada que quedarse mudo."""
    from magnus.voice import backend as backend_module

    otra = tmp_path / "es_ES-otra-high.onnx"
    otra.write_bytes(b"modelo")
    monkeypatch.setattr(backend_module, "_VOICE_DIRS", (tmp_path,))
    assert backend_module.find_piper_model("es_MX-no-descargada") == otra


def test_no_voices_downloaded_returns_none(tmp_path, monkeypatch):
    from magnus.voice import backend as backend_module

    monkeypatch.setattr(backend_module, "_VOICE_DIRS", (tmp_path,))
    assert backend_module.find_piper_model("cualquiera") is None
