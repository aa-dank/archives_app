# Archives Application

An application that provides services related to a UCSC PPDO file server containing archival files, primarily campus construction project files. It provides services related to the file server, facilitating file management, archival processes, and data scraping.

## Table of Contents

- [Introduction](#introduction)
- [Features](#features)
- [Installation](#installation)
- [Usage](#usage)
- [Configuration](#configuration)
- [Maintenance](#maintenance)

## Features

- **File Management Interface**: Allows users to manage and organize files on the server and collects data about file server changes. Offers additional file server change controls based on quantities of files changed. Also maintains a complete PostgreSQL database of files which can be used for search or other SQL functions.
- **Archival Tools**: Provides functionalities for archiving files on the records server and a file archiving inbox for iteratively archiving several files on the file server.
- **Data Scraping**: Scrapes file metadata from the file server to keep the application database updated with the latest information. Scrapes project data from a FileMaker database to maintain updated project data.
- **User Authentication and Roles**: Supports user registration and authentication using Google authentication. Also has role-based access control for application services and file server services.
- **Web-Based Interface**: Offers a user-friendly web interface for interacting with the file server and accessing application features.
- **Maintenance Utilities**: Includes tools for routine maintenance tasks like database backup, file location confirmation, database management, and system logs management.
- **API Endpoints for Programmatic Interaction**: Offers a set of endpoints that allow external programs to interact with the archives application programmatically. This enables automation of tasks, integration with other systems, and access to archival data through RESTful APIs.

## Installation

### Prerequisites

- **Python 3.9 or higher**: Ensure Python is installed on your system.
- **PostgreSQL 15**: Install PostgreSQL for the application's database.
- **Redis**: Install Redis for handling background tasks.
- **Nginx**: Install Nginx as the web server.
- **Supervisord**: Install Supervisord to manage application processes.
- **Git**: For cloning the repository.
- **Samba**: For network storage access to the file server.




    

