FROM python:3.12-slim

WORKDIR /app

# Зависимости ставим отдельным слоем для кеширования
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Код
COPY bot.py storage.py ./

# База по умолчанию лежит в томе /data (чтобы переживала пересборку образа)
ENV DB_PATH=/data/messages.db
VOLUME ["/data"]

# BOT_TOKEN и остальное передаются через переменные окружения / env_file
CMD ["python", "bot.py"]
