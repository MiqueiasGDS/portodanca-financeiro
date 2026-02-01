import os
import sqlite3
import asyncio
from datetime import datetime
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# Configurações - Pegar das variáveis de ambiente
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "SEU_TOKEN_AQUI")
TELEGRAM_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID", "-1234567890"))

# Inicializar banco de dados
def init_db():
    conn = sqlite3.connect('portodanca.db')
    c = conn.cursor()
    
    # Tabela para mensagens do Telegram
    c.execute('''CREATE TABLE IF NOT EXISTS mensagens_telegram
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  message_id INTEGER UNIQUE,
                  chat_id INTEGER,
                  user_name TEXT,
                  user_id INTEGER,
                  texto TEXT,
                  data_mensagem TEXT,
                  processado INTEGER DEFAULT 0)''')
    
    # Tabela de gastos
    c.execute('''CREATE TABLE IF NOT EXISTS gastos
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  data TEXT,
                  descricao TEXT,
                  valor REAL,
                  categoria TEXT,
                  data_registro TEXT,
                  informado_por TEXT,
                  message_id INTEGER)''')
    
    # Tabela para controlar última sincronização
    c.execute('''CREATE TABLE IF NOT EXISTS sync_control
                 (id INTEGER PRIMARY KEY,
                  ultima_sync TEXT)''')
    
    conn.commit()
    conn.close()

# Salvar mensagem do Telegram
async def salvar_mensagem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    
    # Só processar mensagens do grupo específico
    if message.chat.id != TELEGRAM_CHAT_ID:
        return
    
    # Só processar mensagens de texto
    if not message.text:
        return
    
    conn = sqlite3.connect('portodanca.db')
    c = conn.cursor()
    
    user_name = message.from_user.first_name
    if message.from_user.last_name:
        user_name += f" {message.from_user.last_name}"
    
    try:
        c.execute('''INSERT INTO mensagens_telegram 
                     (message_id, chat_id, user_name, user_id, texto, data_mensagem)
                     VALUES (?, ?, ?, ?, ?, ?)''',
                  (message.message_id,
                   message.chat.id,
                   user_name,
                   message.from_user.id,
                   message.text,
                   message.date.isoformat()))
        
        conn.commit()
        print(f"✅ Mensagem salva: {user_name} - {message.text[:50]}")
        
    except sqlite3.IntegrityError:
        # Mensagem já existe
        pass
    except Exception as e:
        print(f"❌ Erro ao salvar mensagem: {e}")
    
    conn.close()

# Rodar bot
async def main():
    init_db()
    
    print("🤖 Iniciando bot do Telegram...")
    print(f"📡 Token configurado: {TELEGRAM_TOKEN[:10]}...")
    print(f"📱 Chat ID: {TELEGRAM_CHAT_ID}")
    
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Handler para todas as mensagens de texto
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, salvar_mensagem))
    
    print("✅ Bot rodando! Aguardando mensagens...")
    
    # Rodar com polling
    await app.initialize()
    await app.start()
    await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    
    # Manter rodando
    while True:
        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
