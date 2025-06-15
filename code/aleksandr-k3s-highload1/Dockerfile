FROM python:3.11

# Create a working directory
WORKDIR /home/hpc/aleksandr

# Copy requirements (if any) and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy your Flask code
COPY server.py .
COPY otlp_json_console_exporter.py .

# Expose port 5001
EXPOSE 5001

# Run the application
CMD [ "python", "server.py" ]
