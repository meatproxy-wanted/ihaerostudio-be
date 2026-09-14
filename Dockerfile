FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends fonts-nanum && rm -rf /var/lib/apt/lists/*
WORKDIR /service
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
COPY app app
RUN useradd --create-home studio && mkdir /service/data && chown studio:studio /service/data
USER studio
ENV DATABASE_PATH=/service/data/studio.sqlite3 PDF_FONT_PATH=/usr/share/fonts/truetype/nanum/NanumGothic.ttf
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
