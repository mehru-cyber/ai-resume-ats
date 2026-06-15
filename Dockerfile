# Dockerfile
FROM python:3.11-slim

# Install system dependencies for PDF generation and file validation
RUN apt-get update && apt-get install -y \
    libcairo2 \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libffi-dev \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# Hugging Face Spaces require running as a non-root user
RUN useradd -m -u 1000 user
USER user
ENV PATH="/home/user/.local/bin:$PATH"

WORKDIR /app

# Copy dependencies and install them
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download AI models during the build step (prevents startup timeouts)
RUN python -m spacy download en_core_web_md
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# Copy the rest of the application code
COPY --chown=user . .

# Expose the specific port Hugging Face looks for
EXPOSE 7860

# Start the FastAPI server on port 7860
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "7860"]