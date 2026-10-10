# Use an official Python slim runtime for a smaller footprint
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# Set timezone to Australian Eastern Time (adjust if you strictly want AWST)
ENV TZ=Australia/Sydney

# Set work directory inside the container
WORKDIR /app

# Install necessary system build tools (required for numpy/scipy wheels if needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies
COPY requirements.txt .
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

# Copy the entire project code into the container
COPY . .

# Expose the port the app runs on
EXPOSE 8090

# Command to run the application using Gunicorn with Uvicorn workers for 24/7 production stability
CMD ["gunicorn", "app:app", "--workers", "2", "--worker-class", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8090", "--timeout", "120"]
