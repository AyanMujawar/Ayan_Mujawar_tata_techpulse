FROM python:3.11-slim
WORKDIR /app
COPY Code/requirements.txt /app/Code/requirements.txt
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir -r /app/Code/requirements.txt
COPY Input_Data /app/Input_Data
COPY Model_Prompts_Config /app/Model_Prompts_Config
COPY Evaluation_Results /app/Evaluation_Results
COPY Code /app/Code
WORKDIR /app/Code
RUN python build_index.py
EXPOSE 8000 8501
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
