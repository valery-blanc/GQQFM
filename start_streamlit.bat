@echo off
cd /d C:\WORK\GQQFM
C:\Users\Val\AppData\Local\Programs\Python\Python311\python.exe -m streamlit run ui\app.py --server.port 8501 --server.headless true --server.address 0.0.0.0
