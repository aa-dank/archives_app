FROM python:3.9-slim-buster
ENV PYTHONUNBUFFERED 1
ENV PYTHONDONTWRITEBYTECODE 1

# Set up Flask application in development mode
ENV FLASK_ENV=development
ENV FLASK_DEBUG=1

# Set the working directory to /app
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    libffi-dev \
    libssl-dev \
    libpango1.0-dev \
    libcairo2-dev \
    libgirepository1.0-dev \
    pkg-config \
    smbclient \
    redis-server \
    nfs-common \
    cifs-utils \
    sudo \
    curl \
    iproute2 \
    iputils-ping


# Mount the Windows Server shares
RUN mkdir -p /app/Data/Archive_Data
RUN mkdir -p /app/Data/PPC_Records
RUN mkdir -p /app/Data/Cannon_Scans
RUN mkdir -p /app/archives_app

COPY . /app/archives_app

# Install any needed packages specified in requirements.txt
RUN pip install --trusted-host pypi.python.org -r /app/archives_app/requirements.txt

# Expose port 5000 for the Flask application
EXPOSE 5000

# Set the environment variable for Flask app
ENV FLASK_APP=run.py