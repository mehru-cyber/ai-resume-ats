# All installs as ROOT (full permissions)
RUN pip install -r requirements.txt      ← installs into /usr/local/lib (system path)
RUN python -m spacy download en_core_web_md  ← works, spacy is now installed
RUN python -c "from sentence_transformers..."  ← works, pre-downloads the model

COPY . .

# Switch to non-root user ONLY for running the app (HF requirement)
RUN useradd -m -u 1000 user && chown -R user:user /app
USER user
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "7860"]