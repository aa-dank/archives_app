FROM python:3.9-slim-buster
ENV PYTHONUNBUFFERED 1
ENV PYTHONDONTWRITEBYTECODE 1

# Set the working directory to /app
WORKDIR /app

# Copy the current directory contents into the container at /app
COPY . /app

ENV SMB_USERNAME=${SMB_USERNAME:-username}
ENV SMB_PASSWORD=${SMB_PASSWORD:-password}
ENV SMB_DOMAIN=${SMB_DOMAIN:-domain}
ENV SMB_VERSION=${SMB_VERSION:-1.0}

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
    cifs-utils

# Create a directory for the mount
RUN mkdir -p /app/Data

# Mount the Windows Server shares
RUN mkdir -p /app/Data/Archive_Data
RUN mkdir -p /app/Data/PPC_Records
RUN mkdir -p /app/Data/Cannon_Scans
RUN mount -t cifs -o "username=${SMB_USERNAME},password=${SMB_PASSWORD},domain=${SMB_DOMAIN},vers=${SMB_VERSION}" //ppdo-fs05.ppcou.ucsc.edu/Archive_Data /app/Data/Archive_Data
RUN mount -t cifs -o "username=${SMB_USERNAME},password=${SMB_PASSWORD},domain=${SMB_DOMAIN},vers=${SMB_VERSION}" //PPDO-ACT-RECORD.ppcou.ucsc.edu/PPC_Records /app/Data/PPC_Records
RUN mount -t cifs -o "username=${SMB_USERNAME},password=${SMB_PASSWORD},domain=${SMB_DOMAIN},vers=${SMB_VERSION}" //ppdo-fs05.ppcou.ucsc.edu/Cannon_Scans /app/Data/Cannon_Scans

# Install any needed packages specified in requirements.txt
RUN pip install --trusted-host pypi.python.org -r requirements.txt

# Expose port 5000 for the Flask application
EXPOSE 5000

# Set the environment variable for Flask app
ENV FLASK_APP=run.py

# Set the user for the container
USER $USERNAME