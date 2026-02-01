import os
import asyncio
from datetime import datetime, timezone, timedelta
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
import psycopg2
from psycopg2.extras import RealDictCursor

# Timezone de Brasília (UTC-3)
BRASILIA_TZ = timezone(timedelta(hours=-3))

def agora_brasilia():
    """Retorna datetime atual em horário de Brasília"""
    return datetime.now(BRASILIA_TZ)

# Configurações - Pegar das variáveis de ambiente
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "SEU_TOKEN_AQUI")
TELEGRAM_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID", "-1234567890"))
DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    """Cria conexão com PostgreSQL"""
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

# Inicializar banco de dados
def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    
    # Tabela para mensagens do Telegram
    c.execute('''CREATE TABLE IF NOT EXISTS mensagens_telegram
                 (id SERIAL PRIMARY KEY,
                  message_id BIGINT UNIQUE,
                  chat_id BIGINT,
                  user_name TEXT,
                  user_id BIGINT,
                  texto TEXT,
                  data_mensagem TIMESTAMP,
                  processado INTEGER DEFAULT 0)''')
    
    # Tabela de gastos
    c.execute('''CREATE TABLE IF NOT EXISTS gastos
                 (id SERIAL PRIMARY KEY,
                  data TIMESTAMP,
                  descricao TEXT,
                  valor DECIMAL(10,2),
                  categoria TEXT,
                  data_registro TIMESTAMP,
                  informado_por TEXT,
                  message_id BIGINT)''')
    
    # Tabela para controlar última sincronização
    c.execute('''CREATE TABLE IF NOT EXISTS sync_control
                 (id INTEGER PRIMARY KEY,
                  ultima_sync TIMESTAMP)''')
    
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
    
    conn = get_db_connection()
    c = conn.cursor()
    
    user_name = message.from_user.first_name
    if message.from_user.last_name:
        user_name += f" {message.from_user.last_name}"
    
    # Converter data da mensagem para Brasília
    data_msg_utc = message.date
    if data_msg_utc.tzinfo is None:
        data_msg_utc = data_msg_utc.replace(tzinfo=timezone.utc)
    data_msg_br = data_msg_utc.astimezone(BRASILIA_TZ)
    
    try:
        c.execute('''INSERT INTO mensagens_telegram 
                     (message_id, chat_id, user_name, user_id, texto, data_mensagem)
                     VALUES (%s, %s, %s, %s, %s, %s)''',
                  (message.message_id,
                   message.chat.id,
                   user_name,
                   message.from_user.id,
                   message.text,
                   data_msg_br))
        
        conn.commit()
        print(f"✅ Mensagem salva [{data_msg_br.strftime('%d/%m/%Y %H:%M')}]: {user_name} - {message.text[:50]}")
        
    except Exception as e:
        if 'duplicate key' not in str(e).lower():
            print(f"❌ Erro ao salvar mensagem: {e}")
    
    conn.close()

# Rodar bot
async def main():
    init_db()
    
    print("🤖 Iniciando bot do Telegram...")
    print(f"📡 Token configurado: {TELEGRAM_TOKEN[:10]}...")
    print(f"📱 Chat ID: {TELEGRAM_CHAT_ID}")
    print(f"🕐 Timezone: Brasília (UTC-3)")
    print(f"🕒 Hora atual: {agora_brasilia().strftime('%d/%m/%Y %H:%M:%S')}")
    
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
