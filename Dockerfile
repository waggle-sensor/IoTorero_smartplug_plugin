FROM python:3.12

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Only the app itself ships; scripts/ configures hardware and is excluded by
# .dockerignore.
COPY app/ .

ENTRYPOINT ["python3", "main.py"]
