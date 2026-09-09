"""Aplicación de juego de MAGNUS: el controlador de la partida y su interfaz web.

Este paquete une los nodos (visión, engine, brazo, voz) en una sola aplicación
lista para jugar, con un panel de control que se abre en el navegador:

    * :mod:`magnus.app.session`     — estado de la partida y el hilo del engine
    * :mod:`magnus.app.settings`    — ajustes persistentes (``magnus_settings.json``)
    * :mod:`magnus.app.arm_bridge`  — supervisor del brazo (apagado / simulado / CyberPi)
    * :mod:`magnus.app.controller`  — ``MagnusController``: el bucle de visión y
      la cola de comandos que la interfaz envía
    * :mod:`magnus.app.server`      — servidor HTTP (librería estándar) que sirve
      la página, el stream de la cámara y la API de comandos
    * ``static/``                   — la interfaz (HTML/CSS/JS, sin dependencias)

El punto de entrada es ``play.py`` en la raíz del repositorio.
"""

from .settings import AppSettings, ARM_MODES

__all__ = ["AppSettings", "ARM_MODES"]
