FROM python:3.11
WORKDIR /home/hpc/aleksandr

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY client.py .
COPY otlp_json_console_exporter.py .

EXPOSE 8001
# CMD ["python", "client.py"]
CMD ["gunicorn", "-w", "4", "-k", "gthread", "-t", "30", "-b", "0.0.0.0:8001", "client:app"]

