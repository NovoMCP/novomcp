"""
Internal Library Loader for S3-based Compound Libraries
Fetches proprietary compound data from S3 and stores in Pinecone
"""

import boto3
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
import tempfile
import os
from rdkit import Chem
from rdkit.Chem import Descriptors
import csv

logger = logging.getLogger(__name__)


class InternalLibraryLoader:
    """
    Loads proprietary compound libraries from S3
    Supports SDF, MOL, and CSV formats
    """

    def __init__(self):
        """Initialize S3 client"""
        self.s3_client = boto3.client('s3', region_name='us-east-1')

    async def load_library(
        self,
        s3_path: str,
        campaign_id: str,
        campaign_metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Load a compound library from S3 and parse it

        Args:
            s3_path: S3 URI (e.g., s3://bucket/path/to/library.sdf)
            campaign_id: Campaign ID for metadata tagging
            campaign_metadata: Additional metadata (target, indication, etc.)

        Returns:
            {
                'success': bool,
                'compounds': List[Dict],
                'total_compounds': int,
                'errors': List[str]
            }
        """
        logger.info(f"Loading internal library from {s3_path} for campaign {campaign_id}")

        try:
            # Parse S3 path
            if not s3_path.startswith('s3://'):
                return {
                    'success': False,
                    'error': 'Invalid S3 path. Must start with s3://',
                    'compounds': [],
                    'total_compounds': 0
                }

            # Extract bucket and key
            path_parts = s3_path.replace('s3://', '').split('/', 1)
            if len(path_parts) != 2:
                return {
                    'success': False,
                    'error': 'Invalid S3 path format. Expected s3://bucket/key',
                    'compounds': [],
                    'total_compounds': 0
                }

            bucket_name = path_parts[0]
            object_key = path_parts[1]

            # Determine file type
            file_ext = object_key.lower().split('.')[-1]

            if file_ext not in ['sdf', 'mol', 'csv']:
                return {
                    'success': False,
                    'error': f'Unsupported file format: {file_ext}. Supported: sdf, mol, csv',
                    'compounds': [],
                    'total_compounds': 0
                }

            # Download file to temporary location
            with tempfile.NamedTemporaryFile(delete=False, suffix=f'.{file_ext}') as tmp_file:
                tmp_path = tmp_file.name

            logger.info(f"Downloading {s3_path} to {tmp_path}")

            try:
                self.s3_client.download_file(bucket_name, object_key, tmp_path)
            except Exception as e:
                logger.error(f"Failed to download from S3: {e}")
                return {
                    'success': False,
                    'error': f'Failed to download from S3: {str(e)}',
                    'compounds': [],
                    'total_compounds': 0
                }

            # Parse file based on format
            compounds = []
            errors = []

            if file_ext in ['sdf', 'mol']:
                compounds, errors = self._parse_sdf_file(tmp_path, campaign_id, campaign_metadata)
            elif file_ext == 'csv':
                compounds, errors = self._parse_csv_file(tmp_path, campaign_id, campaign_metadata)

            # Clean up temp file
            try:
                os.unlink(tmp_path)
            except Exception as e:
                logger.warning(f"Failed to delete temp file {tmp_path}: {e}")

            logger.info(f"Parsed {len(compounds)} compounds from {s3_path}")

            return {
                'success': True,
                'compounds': compounds,
                'total_compounds': len(compounds),
                'errors': errors if errors else None
            }

        except Exception as e:
            logger.error(f"Failed to load internal library: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'compounds': [],
                'total_compounds': 0
            }

    def _parse_sdf_file(
        self,
        file_path: str,
        campaign_id: str,
        campaign_metadata: Dict[str, Any]
    ) -> tuple[List[Dict], List[str]]:
        """
        Parse SDF/MOL file using RDKit

        Returns:
            (compounds, errors)
        """
        compounds = []
        errors = []

        try:
            supplier = Chem.SDMolSupplier(file_path)

            for idx, mol in enumerate(supplier):
                if mol is None:
                    errors.append(f"Failed to parse molecule at index {idx}")
                    continue

                try:
                    # Generate compound data
                    smiles = Chem.MolToSmiles(mol)

                    # Calculate properties
                    properties = {
                        'mw': Descriptors.MolWt(mol),
                        'logp': Descriptors.MolLogP(mol),
                        'hbd': Descriptors.NumHDonors(mol),
                        'hba': Descriptors.NumHAcceptors(mol),
                        'tpsa': Descriptors.TPSA(mol),
                        'rotatable_bonds': Descriptors.NumRotatableBonds(mol),
                        'aromatic_rings': Descriptors.NumAromaticRings(mol)
                    }

                    # Get additional data from SDF properties
                    mol_data = mol.GetPropsAsDict() if hasattr(mol, 'GetPropsAsDict') else {}

                    compound = {
                        'smiles': smiles,
                        'source': 'internal_library',
                        'campaign_id': campaign_id,
                        'properties': properties,
                        'sdf_data': mol_data,
                        'metadata': {
                            **campaign_metadata,
                            'ingested_at': datetime.utcnow().isoformat(),
                            'library_index': idx
                        }
                    }

                    compounds.append(compound)

                except Exception as e:
                    errors.append(f"Failed to process molecule {idx}: {str(e)}")

        except Exception as e:
            errors.append(f"Failed to read SDF file: {str(e)}")

        return compounds, errors

    def _parse_csv_file(
        self,
        file_path: str,
        campaign_id: str,
        campaign_metadata: Dict[str, Any]
    ) -> tuple[List[Dict], List[str]]:
        """
        Parse CSV file with compound data
        Expected columns: smiles, name (optional), activity (optional)

        Returns:
            (compounds, errors)
        """
        compounds = []
        errors = []

        try:
            with open(file_path, 'r') as f:
                reader = csv.DictReader(f)

                # Check for required columns
                if 'smiles' not in reader.fieldnames:
                    errors.append("CSV must contain 'smiles' column")
                    return compounds, errors

                for idx, row in enumerate(reader):
                    smiles = row.get('smiles', '').strip()

                    if not smiles:
                        errors.append(f"Empty SMILES at row {idx}")
                        continue

                    try:
                        # Validate SMILES with RDKit
                        mol = Chem.MolFromSmiles(smiles)

                        if mol is None:
                            errors.append(f"Invalid SMILES at row {idx}: {smiles}")
                            continue

                        # Calculate properties
                        properties = {
                            'mw': Descriptors.MolWt(mol),
                            'logp': Descriptors.MolLogP(mol),
                            'hbd': Descriptors.NumHDonors(mol),
                            'hba': Descriptors.NumHAcceptors(mol),
                            'tpsa': Descriptors.TPSA(mol),
                            'rotatable_bonds': Descriptors.NumRotatableBonds(mol),
                            'aromatic_rings': Descriptors.NumAromaticRings(mol)
                        }

                        # Add any additional CSV columns
                        csv_data = {k: v for k, v in row.items() if k != 'smiles'}

                        compound = {
                            'smiles': smiles,
                            'source': 'internal_library',
                            'campaign_id': campaign_id,
                            'properties': properties,
                            'csv_data': csv_data,
                            'metadata': {
                                **campaign_metadata,
                                'ingested_at': datetime.utcnow().isoformat(),
                                'library_index': idx
                            }
                        }

                        compounds.append(compound)

                    except Exception as e:
                        errors.append(f"Failed to process row {idx}: {str(e)}")

        except Exception as e:
            errors.append(f"Failed to read CSV file: {str(e)}")

        return compounds, errors

    async def store_in_pinecone(
        self,
        compounds: List[Dict],
        campaign_id: str
    ) -> Dict[str, Any]:
        """
        Store parsed compounds in Pinecone for campaign querying

        Args:
            compounds: List of parsed compound dictionaries
            campaign_id: Campaign ID for filtering

        Returns:
            Storage statistics
        """
        logger.info(f"Storing {len(compounds)} compounds in Pinecone for campaign {campaign_id}")

        try:
            from core.pinecone_client import get_pinecone_client
            from ai.embedding_generator import get_embedder

            pinecone_client = get_pinecone_client()
            embedding_gen = get_embedder()

            stored_count = 0
            errors = []

            # Batch compounds (100 at a time)
            batch_size = 100
            for i in range(0, len(compounds), batch_size):
                batch = compounds[i:i + batch_size]

                try:
                    # Generate embeddings for SMILES
                    texts = [
                        f"Compound: {c['smiles']} Properties: MW={c['properties']['mw']:.1f} LogP={c['properties']['logp']:.1f}"
                        for c in batch
                    ]

                    async with embedding_gen:
                        embeddings = await embedding_gen.generate_embeddings(texts)

                    # Prepare vectors for Pinecone
                    vectors = []
                    for idx, compound in enumerate(batch):
                        vector_id = f"internal_{campaign_id}_{compound['metadata']['library_index']}"
                        vectors.append({
                            'id': vector_id,
                            'values': embeddings[idx],
                            'metadata': {
                                'type': 'internal_compound',
                                'campaign_id': campaign_id,
                                'smiles': compound['smiles'],
                                'source': 'internal_library',
                                'properties': compound['properties'],
                                'ingested_at': compound['metadata']['ingested_at']
                            }
                        })

                    # Upsert to Pinecone
                    pinecone_client.upsert(vectors=vectors)
                    stored_count += len(vectors)

                except Exception as e:
                    logger.error(f"Failed to store batch {i}-{i+batch_size}: {e}")
                    errors.append(f"Batch {i}: {str(e)}")

            logger.info(f"Stored {stored_count}/{len(compounds)} compounds in Pinecone")

            return {
                'success': True,
                'stored_count': stored_count,
                'total_compounds': len(compounds),
                'errors': errors if errors else None
            }

        except Exception as e:
            logger.error(f"Failed to store in Pinecone: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'stored_count': 0
            }
