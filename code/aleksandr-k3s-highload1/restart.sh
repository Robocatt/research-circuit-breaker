#!/bin/bash
helm uninstall release2
cd /home/hpc/aleksandr/helm 
sleep 45
helm install release2 .
kubectl delete job k6-loadtest
cd /home/hpc/aleksandr/load
kubectl apply -f k6-configmap.yaml
kubectl apply -f k6-loadtest-job.yaml
sleep 3
kubectl get pods -o wide