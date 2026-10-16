FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Download the embedding model at build time so startup is fast
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/all-MiniLM-L6-v2', cache_dir='/app/model_cache')"
COPY main.py test.py ./
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
