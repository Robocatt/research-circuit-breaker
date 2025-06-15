FROM python:3.11

# Create a working directory
WORKDIR /home/hpc/aleksandr

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the code
COPY client.py .
COPY otlp_json_console_exporter.py .

# Expose the port used by the Flask proxy
EXPOSE 8001

# Run your circuit-breaker client (Flask app)
# CMD ["python", "client.py"]
CMD ["gunicorn", "-w", "4", "-k", "gthread", "-t", "30", "-b", "0.0.0.0:8001", "client:app"]


