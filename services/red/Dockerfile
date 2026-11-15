# Imagen de las Lambdas con ML (procesar-metricas y entrenar). La misma imagen, distinto CMD (ver infra/lado-red.yaml).
FROM public.ecr.aws/lambda/python:3.12
COPY requirements-ml.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements-ml.txt
COPY *.py ${LAMBDA_TASK_ROOT}/
CMD ["procesar_metricas.handler"]
