import logging
from telegram import Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, CallbackContext
import re
import sys
import database
from dotenv import load_dotenv
import os
import asyncio

# Load environment variables
load_dotenv()

# Bot Token from environment variable
TOKEN = os.getenv("BOT_TOKEN")

# Group configuration
ALLOWED_GROUP_ID = -1002165335366  # Updated with your actual group ID from logs
ADMINS = {7951420571, 136817688}

# Maximum warnings before ban
MAX_WARNINGS = 3

# Update logging configuration to be simpler
logging.basicConfig(
    format='%(levelname)s: %(message)s',
    level=logging.INFO  # Change to INFO for less verbose logs
)
logger = logging.getLogger(__name__)

# Anti-Link Regex
LINK_PATTERN = re.compile(r"https?://\S+")

# Add button callback patterns
APPROVE_CALLBACK = "approve_link_{}"
REJECT_CALLBACK = "reject_link_{}"

WELCOME_MESSAGE = """
👋 Selamat datang {username} ke dalam group ini!

📜 Peraturan Kumpulan:
• Sila berkelakuan baik
• Tiada spam
• Tiada link tanpa kebenaran admin
• Hormati semua ahli

🤖 Bot kami akan memantau aktiviti anda.
"""

def is_from_allowed_group(update: Update) -> bool:
    """Check if the message is from the allowed group."""
    if not update.effective_chat:
        logger.warning("No effective_chat in update")
        return False
    
    chat_id = update.effective_chat.id
    is_allowed = chat_id == ALLOWED_GROUP_ID
    logger.info(f"Message from chat ID: {chat_id}, Is allowed: {is_allowed}")
    return is_allowed

async def handle_unauthorized(update: Update):
    """Handle unauthorized usage."""
    chat_id = update.effective_chat.id if update.effective_chat else "Unknown"
    logger.warning(f"Unauthorized access attempt from chat ID: {chat_id}")
    await update.message.reply_text("This bot is configured for a specific group only.")

async def delete_message_later(message, delay_seconds: int):
    """Delete a message after specified delay."""
    try:
        await asyncio.sleep(delay_seconds)
        await message.delete()
    except Exception as e:
        logger.error(f"Error deleting message: {e}")

async def handle_mod_command(update: Update, action_msg: str, error_msg: str = None, delete_after: int = None):
    """
    Handle mod command with flexible message deletion.
    delete_after: seconds after which to delete message. None means keep message.
    """
    try:
        # Store chat_id before deleting command
        chat_id = update.effective_chat.id
        
        # Delete command message immediately
        await update.message.delete()
        
        if error_msg:
            # Always delete error messages after 3 seconds
            error_response = await update.effective_chat.send_message(f"❌ {error_msg}")
            asyncio.create_task(delete_message_later(error_response, 3))
            return
        
        # Send action message
        response = await update.effective_chat.send_message(action_msg)
        
        # Only schedule deletion if delete_after is specified
        if delete_after is not None:
            asyncio.create_task(delete_message_later(response, delete_after))

    except Exception as e:
        logger.error(f"Command error: {e}")

async def mute(update: Update, context: CallbackContext):
    """Mute a user for a specified duration."""
    if not is_from_allowed_group(update) or update.message.from_user.id not in ADMINS:
        return

    try:
        # Check for duration in all args
        duration_arg = None
        for arg in context.args:
            if arg.endswith('m') and arg[:-1].isdigit():
                duration_arg = arg
                break
                
        if not duration_arg:
            await handle_mod_command(update, None, "Sila nyatakan masa. Contoh: /mute @user 10m [sebab]")
            return
            
        target_user = get_target_user(update, context)
        if not target_user:
            await handle_mod_command(update, None, "Sila reply kepada mesej pengguna atau tag mereka.")
            return

        # Parse duration
        try:
            duration = int(duration_arg[:-1])
        except ValueError:
            await handle_mod_command(update, None, "Format masa tidak sah. Gunakan: 10m, 30m, etc.")
            return

        # Get reason
        duration_index = context.args.index(duration_arg)
        reason = " ".join(context.args[duration_index + 1:]) if len(context.args) > duration_index + 1 else None
        
        # Add mute to database and restrict user
        await database.add_mute(
            target_user.id, 
            update.message.from_user.id,
            duration,
            reason
        )
        
        await update.message.chat.restrict_member(
            target_user.id, 
            ChatPermissions(can_send_messages=False)
        )
        
        msg = f"👤 {target_user.first_name} telah dibisukan selama {duration} minit."
        if reason:
            msg += f"\n📝 Sebab: {reason}"
        await handle_mod_command(update, msg)  # Keep mute messages
        
    except Exception as e:
        logger.error(f"Mute error: {str(e)}")
        await handle_mod_command(update, None, str(e))

async def unmute(update: Update, context: CallbackContext):
    """Unmute a user."""
    if not is_from_allowed_group(update) or update.message.from_user.id not in ADMINS:
        return

    try:
        target_user = get_target_user(update, context)
        if not target_user:
            await handle_mod_command(update, None, "Sila reply kepada mesej pengguna atau tag mereka.")
            return
            
        await database.remove_mute(target_user.id)
        await update.message.chat.restrict_member(
            target_user.id,
            ChatPermissions(can_send_messages=True)
        )
        await handle_mod_command(update, f"✅ {target_user.first_name} telah dinyahbisu.")
    except Exception as e:
        await handle_mod_command(update, None, str(e))

async def ban(update: Update, context: CallbackContext):
    """Ban a user."""
    if not is_from_allowed_group(update) or update.message.from_user.id not in ADMINS:
        return

    try:
        target_user = get_target_user(update, context)
        if not target_user:
            await handle_mod_command(update, None, "Sila reply kepada mesej pengguna atau tag mereka.")
            return
            
        reason = " ".join(context.args[1:]) if len(context.args) > 1 else None
        await database.add_ban(target_user.id, update.message.from_user.id, reason)
        await update.message.chat.ban_member(target_user.id)
        
        msg = f"⛔️ {target_user.first_name} telah diharamkan."
        if reason:
            msg += f"\n📝 Sebab: {reason}"
        await handle_mod_command(update, msg)  # No delete_after parameter = keep message
    except Exception as e:
        await handle_mod_command(update, None, str(e))

async def unban(update: Update, context: CallbackContext):
    """Unban a user."""
    if not is_from_allowed_group(update) or update.message.from_user.id not in ADMINS:
        return

    try:
        if not context.args:
            await handle_mod_command(update, None, "Sila nyatakan username. Contoh: /unban @username")
            return
            
        username = context.args[0].replace("@", "")
        user_id = await database.get_user_id_from_username(username)
        if not user_id:
            await handle_mod_command(update, None, "Pengguna tidak dijumpai.")
            return
            
        await database.remove_ban(user_id)
        await update.message.chat.unban_member(user_id)
        await handle_mod_command(update, f"✅ Pengguna telah dinyahlarang.")
    except Exception as e:
        await handle_mod_command(update, None, str(e))

def get_target_user(update: Update, context):
    """Helper function to get target user from command."""
    if update.message.reply_to_message:
        return update.message.reply_to_message.from_user
    elif update.message.entities and len(update.message.entities) > 1:
        entity = update.message.entities[1]
        if entity.type == 'text_mention':
            return entity.user
    return None

async def warn(update: Update, context: CallbackContext):
    """Warn a user."""
    if not is_from_allowed_group(update) or update.message.from_user.id not in ADMINS:
        return

    try:
        target_user = get_target_user(update, context)
        if not target_user:
            await handle_mod_command(update, None, "Sila reply kepada mesej pengguna.")
            return
        
        warn_count = await database.add_warning(target_user.id)
        
        if warn_count >= MAX_WARNINGS:
            await update.message.chat.ban_member(target_user.id)
            await handle_mod_command(update, f"{target_user.first_name} telah diharamkan selepas {MAX_WARNINGS} amaran.")
        else:
            await handle_mod_command(update, f"{target_user.first_name} telah diberi amaran ({warn_count}/{MAX_WARNINGS}).")
    except Exception as e:
        await handle_mod_command(update, None, str(e))

async def track_user_activity(update: Update, context: CallbackContext):
    """Track user messages."""
    if not is_from_allowed_group(update):
        await handle_unauthorized(update)
        return
    user = update.message.from_user
    await database.add_user(user.id, user.username)

async def handle_links(update: Update, context: CallbackContext):
    """Detect links and send to admin for approval."""
    if not is_from_allowed_group(update):
        await handle_unauthorized(update)
        return
        
    try:
        user = update.message.from_user
        message_text = update.message.text
        
        if LINK_PATTERN.search(message_text):
            # First ensure user exists in database
            await database.add_user(user.id, user.username)
            
            # Then add the pending link with original message
            link_id = await database.add_pending_link(
                user_id=user.id,
                link=message_text,
                original_message=message_text
            )
            
            keyboard = [
                [
                    InlineKeyboardButton("✅ Terima", callback_data=APPROVE_CALLBACK.format(link_id)),
                    InlineKeyboardButton("❌ Tolak", callback_data=REJECT_CALLBACK.format(link_id))
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Send message to all admins
            for admin_id in ADMINS:
                try:
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text=f"💬 Mesej dari {user.username}:\n\n{message_text}",
                        reply_markup=reply_markup
                    )
                except Exception as e:
                    logger.error(f"Gagal menghantar notifikasi kepada admin {admin_id}: {e}")
            
            # Delete original message and notify
            await update.message.delete()
            notification = await update.effective_chat.send_message(
                f"Mesej dari {user.username} telah disembunyikan untuk semakan admin."
            )
            # Delete notification after 3 seconds
            asyncio.create_task(delete_message_later(notification, 3))
            
    except Exception as e:
        logger.error(f"Ralat dalam handle_links: {str(e)}", exc_info=True)

async def approve_link(update: Update, context: CallbackContext):
    """Approve a pending link."""
    if not is_from_allowed_group(update) or update.message.from_user.id not in ADMINS:
        return

    if not context.args:
        await handle_mod_command(update, None, "Sila nyatakan ID link. Contoh: /approve 123")
        return

    try:
        link_id = int(context.args[0])
        await database.approve_link(link_id)
        await handle_mod_command(update, f"✅ Link {link_id} telah diluluskan.", delete_after=3)
    except Exception as e:
        await handle_mod_command(update, None, str(e))

async def show_pending_links(update: Update, context: CallbackContext):
    """Show all pending links."""
    if not is_from_allowed_group(update) or update.message.from_user.id not in ADMINS:
        return

    links = await database.get_pending_links()
    if not links:
        await update.message.reply_text("No pending links.")
        return

    msg = "\n".join([f"{link['id']}: {link['link']}" for link in links])
    await update.message.reply_text(f"Pending links:\n{msg}")

async def handle_button(update: Update, context: CallbackContext):
    """Handle button clicks for link approval/rejection."""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id not in ADMINS:
        await query.message.edit_text("Anda tidak mempunyai kebenaran untuk ini.")
        return
    
    try:
        callback_data = query.data
        action = "approve" if "approve" in callback_data else "reject"
        link_id = int(callback_data.split("_")[-1])
        
        if action == "approve":
            link_data = await database.approve_link(link_id)
            await query.message.edit_text(
                f"✅ Mesej telah diterima dan dihantar ke kumpulan."
            )
            # Send original message to group
            await context.bot.send_message(
                chat_id=ALLOWED_GROUP_ID,
                text=link_data['message']  # Send the original message
            )
        else:
            await database.reject_link(link_id)
            await query.message.edit_text("❌ Mesej telah ditolak.")
            
    except Exception as e:
        logger.error(f"Error in handle_button: {e}")
        await query.message.edit_text("❌ Ralat semasa memproses mesej.")

async def get_chat_id(update: Update, context: CallbackContext):
    """Get the current chat ID."""
    chat_id = update.effective_chat.id
    chat_type = update.effective_chat.type
    logger.info(f"Current chat ID: {chat_id}, Type: {chat_type}")
    await update.message.reply_text(f"Chat ID: {chat_id}\nType: {chat_type}")

async def welcome_new_member(update: Update, context: CallbackContext):
    """Welcome new members when they join."""
    if not is_from_allowed_group(update):
        return
        
    for member in update.message.new_chat_members:
        if not member.is_bot:  # Don't welcome bots
            try:
                # Add user to database
                await database.add_user(member.id, member.username)
                # Send welcome message and schedule deletion after 15 minutes
                welcome_msg = await update.message.reply_text(
                    WELCOME_MESSAGE.format(
                        username=member.username or member.first_name
                    )
                )
                asyncio.create_task(delete_message_later(welcome_msg, 900))  # 15 minutes = 900 seconds
            except Exception as e:
                logger.error(f"Error welcoming new member: {e}")

def init_database():
    """Initialize database in a separate process."""
    try:
        import subprocess
        subprocess.run([sys.executable, '-c', 
            'import asyncio; import database; asyncio.run(database.init_db())'])
        return True
    except Exception as e:
        logging.error(f"Database initialization failed: {e}")
        return False

if __name__ == "__main__":
    try:
        # Initialize database first in a separate process
        if not init_database():
            sys.exit(1)

        # Create and configure application
        app = Application.builder().token(TOKEN).build()
        
        # Add command handlers
        app.add_handler(CommandHandler("mute", mute))
        app.add_handler(CommandHandler("unmute", unmute))
        app.add_handler(CommandHandler("ban", ban))
        app.add_handler(CommandHandler("unban", unban))
        app.add_handler(CommandHandler("approve", approve_link))
        app.add_handler(CommandHandler("pending", show_pending_links))
        app.add_handler(CommandHandler("warn", warn))
        app.add_handler(CommandHandler("chatid", get_chat_id))
        
        # Add callback handler for buttons
        app.add_handler(CallbackQueryHandler(handle_button))
        
        # Add message handlers (order matters!)
        app.add_handler(MessageHandler(
            filters.StatusUpdate.NEW_CHAT_MEMBERS,
            welcome_new_member
        ))
        app.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.Regex(LINK_PATTERN),
            handle_links
        ))
        app.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            track_user_activity
        ))

        # Start bot with basic configuration
        logging.info(f"Bot configured for group ID: {ALLOWED_GROUP_ID}")
        logging.info("Starting bot...")
        app.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
            close_loop=False
        )

    except KeyboardInterrupt:
        logging.info("Bot dihentikan oleh pengguna")
    except Exception as e:
        logging.error(f"Ralat fatal: {e}", exc_info=True)
    finally:
        logging.info("Bot ditutup")