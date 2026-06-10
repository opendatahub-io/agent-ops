FROM registry.access.redhat.com/ubi9/python-312:latest

WORKDIR /opt/app-root
COPY requirements.txt .
RUN python3 -m pip install --upgrade pip setuptools wheel && \
    python3 -m pip install --no-cache-dir -r requirements.txt

# Pre-download the embedding model so it's baked into the image
# (avoids a HuggingFace download on first request).
ENV HF_HOME=/opt/app-root/hf_cache
RUN python3 -c "from langchain_huggingface import HuggingFaceEmbeddings; \
    HuggingFaceEmbeddings(model_name='nomic-ai/nomic-embed-text-v1.5', model_kwargs={'trust_remote_code': True})"

COPY redbank-mcp/ redbank-mcp/

WORKDIR /opt/app-root/redbank-mcp

ENV PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["python3", "mcp_server.py"]
