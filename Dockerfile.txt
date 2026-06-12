FROM python:3.11

RUN apt-get update && apt-get install -y libmagic1

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . ./

CMD uvicorn backend.main:app --host 0.0.0.0 --port $PORT