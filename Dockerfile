FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
COPY prompt prompt
ENV PYTHONUNBUFFERED=1
VOLUME /srv/data
CMD ["python", "-m", "app.main"]
