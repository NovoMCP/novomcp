"""
Structure Manager for OpenFold3 Integration
Handles protein structure prediction via OpenFold3 service with async job queue
"""

import logging
import os
import asyncio
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
import httpx
import json
import redis.asyncio as redis

logger = logging.getLogger(__name__)

# OpenFold3 service configuration (migrated to Azure)
OPENFOLD3_URL = os.getenv("OPENFOLD3_URL", "")
OPENFOLD3_API_KEY = os.getenv("OPENFOLD3_API_KEY")  # Set via env var
REDIS_URL = os.getenv("REDIS_URL") or os.getenv("AZURE_REDIS_URL", "")

# Prediction timeout (15 minutes max)
PREDICTION_TIMEOUT = 900


class StructurePredictionError(Exception):
    """Raised when structure prediction fails"""
    pass


async def predict_protein_structure(
    sequence: str,
    request_id: Optional[str] = None,
    msa_alignment: Optional[str] = None,
    output_format: str = "pdb",
    poll_interval: int = 10
) -> Dict[str, Any]:
    """
    Predict protein structure using OpenFold3 service

    Args:
        sequence: Protein amino acid sequence
        request_id: Optional request identifier
        msa_alignment: Optional MSA alignment in CSV format
        output_format: Output format (pdb or cif)
        poll_interval: Polling interval in seconds (default: 10)

    Returns:
        Dict containing:
            - structure: PDB/CIF structure content
            - confidence_scores: Confidence metrics
            - job_id: OpenFold3 job ID
            - completed_at: Completion timestamp

    Raises:
        StructurePredictionError: If prediction fails or times out
    """
    logger.info(f"Predicting structure for sequence: {sequence[:50]}...")

    # Build prediction request
    molecules = [{
        "type": "protein",
        "id": "A",
        "sequence": sequence
    }]

    # Add MSA if provided
    if msa_alignment:
        molecules[0]["msa"] = {
            "main_db": {
                "csv": {
                    "alignment": msa_alignment,
                    "format": "csv"
                }
            }
        }

    request_data = {
        "request_id": request_id,
        "molecules": molecules,
        "output_format": output_format
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Submit prediction job
            logger.info(f"Submitting prediction to OpenFold3: {OPENFOLD3_URL}/predict")
            response = await client.post(
                f"{OPENFOLD3_URL}/predict",
                headers={
                    "x-api-key": OPENFOLD3_API_KEY,
                    "Content-Type": "application/json"
                },
                json=request_data
            )

            if response.status_code != 200:
                raise StructurePredictionError(
                    f"OpenFold3 submission failed: HTTP {response.status_code} - {response.text}"
                )

            submit_result = response.json()
            job_id = submit_result["job_id"]
            logger.info(f"OpenFold3 job submitted: {job_id}")

            # Poll for completion
            result = await _poll_prediction_status(job_id, poll_interval)
            return result

    except httpx.TimeoutException:
        raise StructurePredictionError("Timeout submitting prediction to OpenFold3")
    except httpx.HTTPError as e:
        raise StructurePredictionError(f"HTTP error calling OpenFold3: {str(e)}")
    except Exception as e:
        logger.error(f"Error predicting structure: {e}", exc_info=True)
        raise StructurePredictionError(f"Structure prediction failed: {str(e)}")


async def _poll_prediction_status(
    job_id: str,
    poll_interval: int = 10
) -> Dict[str, Any]:
    """
    Poll OpenFold3 job status until completion

    Args:
        job_id: OpenFold3 job identifier
        poll_interval: Seconds between polls

    Returns:
        Dict with structure and metadata

    Raises:
        StructurePredictionError: If job fails or times out
    """
    start_time = datetime.utcnow()
    attempt = 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            attempt += 1
            elapsed = (datetime.utcnow() - start_time).total_seconds()

            # Check timeout
            if elapsed > PREDICTION_TIMEOUT:
                raise StructurePredictionError(
                    f"Structure prediction timed out after {PREDICTION_TIMEOUT}s"
                )

            # Poll status
            try:
                status_response = await client.get(
                    f"{OPENFOLD3_URL}/status/{job_id}",
                    headers={"x-api-key": OPENFOLD3_API_KEY}
                )

                if status_response.status_code != 200:
                    raise StructurePredictionError(
                        f"Failed to check status: HTTP {status_response.status_code}"
                    )

                status_data = status_response.json()
                status = status_data["status"]

                logger.info(f"OpenFold3 job {job_id} status: {status} (attempt {attempt}, {elapsed:.0f}s elapsed)")

                if status == "completed":
                    # Get result
                    result_response = await client.get(
                        f"{OPENFOLD3_URL}/result/{job_id}",
                        headers={"x-api-key": OPENFOLD3_API_KEY}
                    )

                    if result_response.status_code != 200:
                        raise StructurePredictionError(
                            f"Failed to get result: HTTP {result_response.status_code}"
                        )

                    result_data = result_response.json()
                    return {
                        "structure": result_data["structure"],
                        "confidence_scores": result_data.get("confidence_scores"),
                        "job_id": job_id,
                        "completed_at": result_data["completed_at"],
                        "format": result_data["format"]
                    }

                elif status == "failed":
                    error = status_data.get("error", "Unknown error")
                    raise StructurePredictionError(f"OpenFold3 prediction failed: {error}")

                # Still running, wait and retry
                await asyncio.sleep(poll_interval)

            except httpx.HTTPError as e:
                logger.warning(f"HTTP error polling status (attempt {attempt}): {e}")
                await asyncio.sleep(poll_interval)
                continue


async def subscribe_to_prediction_updates(
    job_id: str,
    callback: callable
) -> None:
    """
    Subscribe to real-time prediction updates via Redis pub/sub

    Args:
        job_id: OpenFold3 job identifier
        callback: Async function to call with updates (receives dict)

    Note:
        This is a blocking call that listens until the job completes or fails
    """
    try:
        redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        pubsub = redis_client.pubsub()

        channel = f"openfold3:job:{job_id}"
        await pubsub.subscribe(channel)

        logger.info(f"Subscribed to OpenFold3 updates: {channel}")

        async for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    data = json.loads(message["data"])
                    await callback(data)

                    # Stop listening if job completed or failed
                    if data.get("status") in ["completed", "failed"]:
                        break

                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON in Redis message: {message['data']}")

        await pubsub.unsubscribe(channel)
        await redis_client.close()

    except Exception as e:
        logger.error(f"Error subscribing to prediction updates: {e}", exc_info=True)


async def predict_protein_complex(
    protein_sequence: str,
    ligand_smiles: Optional[str] = None,
    dna_sequences: Optional[List[str]] = None,
    rna_sequences: Optional[List[str]] = None,
    output_format: str = "pdb"
) -> Dict[str, Any]:
    """
    Predict protein-ligand or protein-nucleic acid complex structure

    Args:
        protein_sequence: Protein amino acid sequence
        ligand_smiles: Optional ligand SMILES string
        dna_sequences: Optional list of DNA sequences
        rna_sequences: Optional list of RNA sequences
        output_format: Output format (pdb or cif)

    Returns:
        Dict with predicted complex structure and metadata
    """
    logger.info("Predicting complex structure with protein and ligands/nucleic acids")

    molecules = [{
        "type": "protein",
        "id": "A",
        "sequence": protein_sequence
    }]

    # Add ligand
    if ligand_smiles:
        molecules.append({
            "type": "ligand",
            "id": "L1",
            "smiles": ligand_smiles
        })

    # Add DNA sequences
    if dna_sequences:
        for i, seq in enumerate(dna_sequences):
            molecules.append({
                "type": "dna",
                "id": f"D{i+1}",
                "sequence": seq
            })

    # Add RNA sequences
    if rna_sequences:
        for i, seq in enumerate(rna_sequences):
            molecules.append({
                "type": "rna",
                "id": f"R{i+1}",
                "sequence": seq
            })

    request_data = {
        "molecules": molecules,
        "output_format": output_format
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{OPENFOLD3_URL}/predict",
                headers={
                    "x-api-key": OPENFOLD3_API_KEY,
                    "Content-Type": "application/json"
                },
                json=request_data
            )

            if response.status_code != 200:
                raise StructurePredictionError(
                    f"Complex prediction failed: HTTP {response.status_code}"
                )

            submit_result = response.json()
            job_id = submit_result["job_id"]

            # Poll for completion
            result = await _poll_prediction_status(job_id)
            return result

    except Exception as e:
        logger.error(f"Error predicting complex: {e}", exc_info=True)
        raise StructurePredictionError(f"Complex prediction failed: {str(e)}")


async def check_service_health() -> Dict[str, Any]:
    """
    Check OpenFold3 service health

    Returns:
        Dict with service status
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{OPENFOLD3_URL}/health")
            if response.status_code == 200:
                return response.json()
            else:
                return {
                    "status": "unhealthy",
                    "error": f"HTTP {response.status_code}"
                }
    except Exception as e:
        logger.error(f"OpenFold3 health check failed: {e}")
        return {
            "status": "unhealthy",
            "error": str(e)
        }
