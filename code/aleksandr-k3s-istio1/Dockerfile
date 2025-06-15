FROM python:3.12
WORKDIR /home/hpc/aleksandr
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .
COPY otlp_json_console_exporter.py .
EXPOSE 5001
# CMD ["python", "server.py"]
CMD ["gunicorn", "-w", "4", "-k", "gthread", "-t", "30", "-b", "0.0.0.0:5001", "server:app"]

