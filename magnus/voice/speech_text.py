"""Traducción de notación de ajedrez a texto **hablable** en español.

Un motor de voz lee fatal la notación de ajedrez: "Cxf3" no se pronuncia, y
"g1" puede salir como "ge uno", "gé uno" o directamente mal según la voz.  Por
eso nunca se le pasa notación cruda al TTS: todo pasa antes por aquí.

    "g1"  -> "ge uno"
    "N"   -> "caballo"
    MoveResponse(e2e4) -> "Muevo el peón de e dos a e cuatro."

Módulo **puro**: sin audio, sin dependencias más allá de los mensajes de
``magnus.core``.  Así la pronunciación se puede testear sin tarjeta de sonido.
"""

from __future__ import annotations

from typing import Optional

from ..core.messages import MoveResponse

# Cómo se pronuncia cada columna.  Las letras que en español ya se leen solas
# ("a", "e") se dejan tal cual; las demás se escriben como se pronuncian para
# que el TTS no deletree en inglés ni invente.
SPOKEN_FILES: dict[str, str] = {
    "a": "a",
    "b": "be",
    "c": "ce",
    "d": "de",
    "e": "e",
    "f": "efe",
    "g": "ge",
    "h": "hache",
}

SPOKEN_RANKS: dict[str, str] = {
    "1": "uno",
    "2": "dos",
    "3": "tres",
    "4": "cuatro",
    "5": "cinco",
    "6": "seis",
    "7": "siete",
    "8": "ocho",
}

# Nombre de cada pieza (el símbolo FEN en mayúscula identifica el tipo).
PIECE_NAMES: dict[str, str] = {
    "P": "peón",
    "N": "caballo",
    "B": "alfil",
    "R": "torre",
    "Q": "dama",
    "K": "rey",
}

# Artículo determinado de cada pieza, para construir frases naturales.
PIECE_ARTICLES: dict[str, str] = {
    "P": "el",
    "N": "el",
    "B": "el",
    "R": "la",
    "Q": "la",
    "K": "el",
}

COLOR_NAMES: dict[str, str] = {"white": "blancas", "black": "negras"}


class SpeechTextError(ValueError):
    """No se pudo convertir la notación a texto hablado."""


def square_to_speech(square: str) -> str:
    """``"g1"`` -> ``"ge uno"``."""
    square = square.strip().lower()
    if len(square) != 2 or square[0] not in SPOKEN_FILES or square[1] not in SPOKEN_RANKS:
        raise SpeechTextError(f"Casilla inválida: {square!r}.")
    return f"{SPOKEN_FILES[square[0]]} {SPOKEN_RANKS[square[1]]}"


def piece_name(symbol: str) -> str:
    """``"N"`` o ``"n"`` -> ``"caballo"``."""
    key = symbol.strip().upper()
    if key not in PIECE_NAMES:
        raise SpeechTextError(f"Pieza inválida: {symbol!r}.")
    return PIECE_NAMES[key]


def piece_with_article(symbol: str) -> str:
    """``"R"`` -> ``"la torre"`` (para encajar en las frases)."""
    key = symbol.strip().upper()
    if key not in PIECE_NAMES:
        raise SpeechTextError(f"Pieza inválida: {symbol!r}.")
    return f"{PIECE_ARTICLES[key]} {PIECE_NAMES[key]}"


def promotion_name(promotion: Optional[str]) -> Optional[str]:
    """``"q"`` -> ``"dama"``; ``None`` si no hay promoción."""
    return piece_name(promotion) if promotion else None


def _from_preposition(spoken_square: str) -> str:
    """``"de de cinco"`` suena a tartamudeo -> ``"desde de cinco"``."""
    return "desde" if spoken_square.startswith("de ") else "de"


def _to_preposition(spoken_square: str) -> str:
    """``"a a ocho"`` suena a tartamudeo -> ``"hasta a ocho"``."""
    return "hasta" if spoken_square.startswith("a ") else "a"


def describe_move(resp: MoveResponse, speaker: str = "robot") -> str:
    """Narra una jugada completa a partir de su :class:`MoveResponse`.

    Args:
        resp: la jugada, con todos sus metadatos ya calculados por el engine.
        speaker: ``"robot"`` para narrar la jugada propia ("Muevo…"), o
            ``"human"`` para narrar la del rival ("Moviste…").

    No re-deduce nada de ajedrez: todo sale de los campos de ``MoveResponse``.
    """
    if speaker not in ("robot", "human"):
        raise SpeechTextError(f"Hablante inválido: {speaker!r}.")
    yo = speaker == "robot"

    # Enroque: se nombra por su nombre, no casilla por casilla.
    if resp.is_castling:
        lado = "corto" if resp.is_kingside_castle else "largo"
        frase = f"{'Enroco' if yo else 'Enrocaste'} {lado}."
        return frase + _check_suffix(resp, yo)

    if not resp.from_square or not resp.to_square:
        raise SpeechTextError("La jugada no tiene casillas de origen y destino.")

    pieza = piece_with_article(resp.piece) if resp.piece else "la pieza"
    origen = square_to_speech(resp.from_square)
    destino = square_to_speech(resp.to_square)

    if resp.is_capture:
        capturada = (
            piece_with_article(resp.captured_piece) if resp.captured_piece else "una pieza"
        )
        verbo = "Capturo" if yo else "Capturaste"
        frase = (f"{verbo} {capturada} en {destino} con {pieza} "
                 f"{_from_preposition(origen)} {origen}")
        if resp.is_en_passant:
            frase += ", al paso"
        frase += "."
    else:
        verbo = "Muevo" if yo else "Moviste"
        frase = (f"{verbo} {pieza} {_from_preposition(origen)} {origen} "
                 f"{_to_preposition(destino)} {destino}.")

    if resp.promotion:
        sujeto = "Mi peón" if yo else "Tu peón"
        frase += f" {sujeto} corona: ahora es {piece_with_article(resp.promotion)}."

    return frase + _check_suffix(resp, yo)


def _check_suffix(resp: MoveResponse, yo: bool) -> str:
    """Coletilla de jaque / jaque mate, si la jugada lo da."""
    if resp.is_checkmate:
        return " Jaque mate."
    if resp.is_check:
        return " Jaque." if yo else " Jaque a mi rey."
    return ""
