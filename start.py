import subprocess
import threading
import time

def run_bot():
    """Roda o bot do Telegram em background"""
    time.sleep(5)  # Aguarda 5 segundos
    subprocess.run(["python", "telegram_bot.py"])

def run_app():
    """Roda o Streamlit app"""
    subprocess.run([
        "streamlit", "run", "app.py",
        "--server.port=8501",
        "--server.address=0.0.0.0"
    ])

if __name__ == "__main__":
    # Iniciar bot em thread separada
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    print("🤖 Bot do Telegram iniciado em background")
    print("🌐 Iniciando app web...")
    
    # Rodar app (bloqueia aqui)
    run_app()
