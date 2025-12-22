"""
Bot de Telegram usando aiogram con ejemplos de mensajes y teclados.
"""

import asyncio
import logging
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    CallbackQuery,
)

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TelegramBot:
    """Cliente de bot de Telegram con aiogram."""

    def __init__(self, token: str):
        """
        Inicializar bot de Telegram.

        Args:
            token: Token del bot de Telegram
        """
        self.bot = Bot(token=token)
        self.dp = Dispatcher()
        self._setup_handlers()

    def _setup_handlers(self):
        """Configurar manejadores de mensajes."""
        # Comando /start
        self.dp.message.register(self.cmd_start, Command("start"))

        # Comando /inline para mostrar teclado inline
        self.dp.message.register(self.cmd_inline, Command("inline"))

        # Comando /reply para mostrar teclado reply
        self.dp.message.register(self.cmd_reply, Command("reply"))

        # Comando /notify para enviar notificación
        self.dp.message.register(self.cmd_notify, Command("notify"))

        # Manejador para callbacks de botones inline
        self.dp.callback_query.register(self.handle_callback)

        # Manejador para mensajes de texto normales
        self.dp.message.register(self.handle_message, F.text)

    async def cmd_start(self, message: Message):
        """Manejador del comando /start."""
        await message.answer(
            "¡Hola! 👋\n\n"
            "Comandos disponibles:\n"
            "/inline - Muestra teclado inline\n"
            "/reply - Muestra teclado reply\n"
            "/notify - Envía una notificación\n\n"
            "Envía cualquier mensaje y te responderé."
        )

    async def cmd_inline(self, message: Message):
        """Mostrar ejemplo de teclado inline."""
        # Crear teclado inline
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Opción 1", callback_data="option_1"),
                    InlineKeyboardButton(text="❌ Opción 2", callback_data="option_2"),
                ],
                [
                    InlineKeyboardButton(text="ℹ️ Opción 3", callback_data="option_3"),
                ],
                [
                    InlineKeyboardButton(
                        text="🔗 Abrir URL", url="https://github.com/aiogram/aiogram"
                    ),
                ],
            ]
        )

        await message.answer(
            "Este es un <b>teclado inline</b> 🎹\n\n"
            "Los botones aparecen debajo del mensaje y pueden:\n"
            "• Enviar callbacks\n"
            "• Abrir URLs\n"
            "• Cambiar a otros bots",
            parse_mode="HTML",
            reply_markup=keyboard,
        )

    async def cmd_reply(self, message: Message):
        """Mostrar ejemplo de teclado reply."""
        # Crear teclado reply
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [
                    KeyboardButton(text="🔴 Rojo"),
                    KeyboardButton(text="🟢 Verde"),
                ],
                [
                    KeyboardButton(text="🔵 Azul"),
                    KeyboardButton(text="🟡 Amarillo"),
                ],
                [
                    KeyboardButton(
                        text="📍 Compartir ubicación", request_location=True
                    ),
                ],
                [
                    KeyboardButton(text="📞 Compartir contacto", request_contact=True),
                ],
            ],
            resize_keyboard=True,  # Ajustar tamaño del teclado
            one_time_keyboard=False,  # Mantener visible después de usarlo
            input_field_placeholder="Elige una opción...",  # Texto placeholder
        )

        await message.answer(
            "Este es un <b>teclado reply</b> ⌨️\n\n"
            "Los botones reemplazan el teclado normal y pueden:\n"
            "• Enviar texto predefinido\n"
            "• Solicitar ubicación\n"
            "• Solicitar contacto",
            parse_mode="HTML",
            reply_markup=keyboard,
        )

    async def cmd_notify(self, message: Message):
        """Enviar notificación al usuario."""
        # Enviar mensaje con notificación
        await message.answer(
            "🔔 <b>Notificación importante!</b>\n\n"
            "Este mensaje llegará con sonido/vibración.",
            parse_mode="HTML",
            disable_notification=False,
        )

        # Enviar mensaje sin notificación (silencioso)
        await asyncio.sleep(1)
        await message.answer(
            "🔕 Este mensaje es <i>silencioso</i>...",
            parse_mode="HTML",
            disable_notification=True,
        )

    async def handle_callback(self, callback: CallbackQuery):
        """Manejador para callbacks de botones inline."""
        data = callback.data

        responses = {
            "option_1": "✅ Seleccionaste la Opción 1",
            "option_2": "❌ Seleccionaste la Opción 2",
            "option_3": "ℹ️ Seleccionaste la Opción 3",
        }

        response_text = responses.get(data, f"Callback desconocido: {data}")

        # Responder al callback (quita el icono de "cargando")
        await callback.answer(
            text=response_text,
            show_alert=False,  # True = modal, False = toast
        )

        # Editar el mensaje original
        await callback.message.edit_text(
            f"{response_text}\n\n"
            f"User ID: {callback.from_user.id}\n"
            f"Callback ID: {callback.id}",
            parse_mode="HTML",
        )

    async def handle_message(self, message: Message):
        """Manejador para mensajes de texto normales."""
        user_text = message.text
        user_id = message.from_user.id
        username = message.from_user.username or "Sin username"

        # Responder al mensaje
        response = (
            f"📨 <b>Recibí tu mensaje:</b>\n\n"
            f"<code>{user_text}</code>\n\n"
            f"👤 Usuario: @{username}\n"
            f"🆔 ID: {user_id}\n"
            f"📏 Longitud: {len(user_text)} caracteres"
        )

        await message.reply(
            response,
            parse_mode="HTML",
        )

    async def send_message(
        self,
        chat_id: int,
        text: str,
        parse_mode: Optional[str] = None,
        reply_markup=None,
    ):
        """
        Enviar mensaje a un chat específico.

        Args:
            chat_id: ID del chat de destino
            text: Texto del mensaje
            parse_mode: Modo de parseo (HTML, Markdown)
            reply_markup: Teclado inline o reply
        """
        return await self.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )

    async def start(self):
        """Iniciar el bot."""
        logger.info("Bot iniciado")
        await self.dp.start_polling(self.bot)

    async def stop(self):
        """Detener el bot."""
        logger.info("Deteniendo bot...")
        await self.bot.session.close()


async def main():
    """Función principal de ejemplo."""
    # Reemplaza con tu token real
    BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"

    bot = TelegramBot(token=BOT_TOKEN)

    try:
        await bot.start()
    except KeyboardInterrupt:
        await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
