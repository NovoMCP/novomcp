"""
Real API Client for Literature Monitoring
Fetches actual data from PubMed, USPTO, ClinicalTrials.gov, and other sources
"""

import aiohttp
import asyncio
import xml.etree.ElementTree as ET
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import json
import logging
import os
import boto3
from botocore.exceptions import ClientError
import feedparser
from urllib.parse import quote

logger = logging.getLogger(__name__)


class LiteratureAPIClient:
    """Real API connections for literature monitoring"""

    def __init__(self):
        """Initialize with API keys from AWS Secrets Manager"""
        self.api_keys = self._load_api_keys()
        self.session = None

    def _load_api_keys(self) -> Dict[str, str]:
        """Load API keys from AWS Secrets Manager with environment fallback"""
        secrets = {}

        # First try environment variables (for local development)
        ncbi_key = os.environ.get('NCBI_API_KEY')
        uspto_key = os.environ.get('USPTO_API_KEY')

        if ncbi_key and uspto_key:
            secrets['ncbi'] = ncbi_key
            secrets['uspto'] = uspto_key
            logger.info("API keys loaded from environment variables")
            return secrets

        # Try AWS Secrets Manager for production
        try:
            client = boto3.client('secretsmanager', region_name='us-east-1')

            # Load NCBI API key
            try:
                response = client.get_secret_value(SecretId='literature/ncbi-api-key')
                secrets['ncbi'] = response['SecretString']
                logger.info("NCBI API key loaded from AWS Secrets Manager")
            except ClientError as e:
                logger.warning(f"Failed to load NCBI key from Secrets Manager: {e}")

            # Load USPTO API key
            try:
                response = client.get_secret_value(SecretId='literature/uspto-api-key')
                secrets['uspto'] = response['SecretString']
                logger.info("USPTO API key loaded from AWS Secrets Manager")
            except ClientError as e:
                logger.warning(f"Failed to load USPTO key from Secrets Manager: {e}")

            if secrets.get('ncbi') and secrets.get('uspto'):
                logger.info("All API keys loaded successfully from AWS Secrets Manager")
                return secrets

        except ClientError as e:
            logger.error(f"Failed to connect to AWS Secrets Manager: {e}")

        # If we still don't have keys, log error
        if not secrets.get('ncbi') or not secrets.get('uspto'):
            logger.error("Failed to load API keys from both environment and AWS Secrets Manager")
            logger.error("Please set NCBI_API_KEY and USPTO_API_KEY environment variables or configure AWS Secrets")
            # Return empty secrets to avoid hardcoded keys
            if not secrets.get('ncbi'):
                secrets['ncbi'] = ''
            if not secrets.get('uspto'):
                secrets['uspto'] = ''

        return secrets

    async def __aenter__(self):
        """Async context manager entry"""
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self.session:
            await self.session.close()

    async def fetch_pubmed_papers(self, query: str, max_results: int = 100, days_back: int = 30) -> List[Dict]:
        """
        Fetch real papers from PubMed using E-utilities API

        Args:
            query: Search query
            max_results: Maximum number of results
            days_back: How many days back to search

        Returns:
            List of paper dictionaries
        """
        papers = []

        # Check if API key is available
        if not self.api_keys.get('ncbi'):
            logger.error("NCBI API key not available, cannot fetch PubMed papers")
            return []

        try:
            # Step 1: Search for PMIDs
            search_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
            search_params = {
                'db': 'pubmed',
                'term': query,
                'retmax': max_results,
                'retmode': 'json',
                'reldate': days_back,
                'datetype': 'pdat'
            }

            # Only add API key if available
            if self.api_keys['ncbi']:
                search_params['api_key'] = self.api_keys['ncbi']

            async with self.session.get(search_url, params=search_params) as response:
                if response.status == 200:
                    data = await response.json()
                    id_list = data.get('esearchresult', {}).get('idlist', [])

                    if id_list:
                        # Step 2: Fetch paper details
                        fetch_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
                        fetch_params = {
                            'db': 'pubmed',
                            'id': ','.join(id_list[:50]),  # Limit to 50 papers
                            'retmode': 'xml',
                            'api_key': self.api_keys['ncbi']
                        }

                        async with self.session.get(fetch_url, params=fetch_params) as fetch_response:
                            if fetch_response.status == 200:
                                xml_data = await fetch_response.text()
                                papers = self._parse_pubmed_xml(xml_data)

            logger.info(f"Fetched {len(papers)} papers from PubMed for query: {query}")

        except Exception as e:
            logger.error(f"PubMed fetch error: {e}")

        return papers

    def _parse_pubmed_xml(self, xml_data: str) -> List[Dict]:
        """Parse PubMed XML response"""
        papers = []

        try:
            root = ET.fromstring(xml_data)

            for article in root.findall('.//PubmedArticle'):
                paper = {}

                # Get PMID
                pmid_elem = article.find('.//PMID')
                if pmid_elem is not None:
                    paper['pmid'] = pmid_elem.text
                    paper['id'] = f"pubmed_{pmid_elem.text}"

                # Get title
                title_elem = article.find('.//ArticleTitle')
                if title_elem is not None:
                    paper['title'] = title_elem.text

                # Get abstract
                abstract_texts = []
                for abstract_elem in article.findall('.//AbstractText'):
                    if abstract_elem.text:
                        abstract_texts.append(abstract_elem.text)
                paper['abstract'] = ' '.join(abstract_texts)

                # Get authors
                authors = []
                for author in article.findall('.//Author'):
                    last_name = author.find('LastName')
                    fore_name = author.find('ForeName')
                    if last_name is not None and fore_name is not None:
                        authors.append(f"{fore_name.text} {last_name.text}")
                paper['authors'] = authors[:5]  # First 5 authors

                # Get journal
                journal_elem = article.find('.//Journal/Title')
                if journal_elem is not None:
                    paper['journal'] = journal_elem.text

                # Get publication date
                pub_date = article.find('.//PubDate')
                if pub_date is not None:
                    year = pub_date.find('Year')
                    month = pub_date.find('Month')
                    day = pub_date.find('Day')
                    date_parts = []
                    if year is not None:
                        date_parts.append(year.text)
                    if month is not None:
                        date_parts.append(month.text)
                    if day is not None:
                        date_parts.append(day.text)
                    paper['date'] = '-'.join(date_parts)

                # Get keywords
                keywords = []
                for keyword in article.findall('.//Keyword'):
                    if keyword.text:
                        keywords.append(keyword.text)
                paper['keywords'] = keywords

                paper['source'] = 'pubmed'
                papers.append(paper)

        except Exception as e:
            logger.error(f"Error parsing PubMed XML: {e}")

        return papers

    async def fetch_patents(self, query: str, max_results: int = 50) -> List[Dict]:
        """
        Fetch patents from USPTO API

        Args:
            query: Search query
            max_results: Maximum number of results

        Returns:
            List of patent dictionaries
        """
        patents = []

        try:
            url = "https://api.uspto.gov/api/v1/patent/applications/search"
            params = {
                'q': query,
                'offset': 0,
                'limit': max_results
            }
            headers = {
                'X-API-Key': self.api_keys['uspto'],
                'Accept': 'application/json'
            }

            async with self.session.get(url, params=params, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()

                    for item in data.get('results', []):
                        patent = {
                            'source': 'uspto',
                            'id': f"patent_{item.get('applicationNumber', '')}",
                            'patent_number': item.get('applicationNumber'),
                            'title': item.get('inventionTitle'),
                            'abstract': item.get('abstractText', [{}])[0].get('text', '') if item.get('abstractText') else '',
                            'applicant': item.get('applicantName', ''),
                            'filing_date': item.get('filingDate', ''),
                            'status': item.get('applicationStatusCode', ''),
                            'inventors': item.get('inventorNameArrayText', [])
                        }
                        patents.append(patent)

            logger.info(f"Fetched {len(patents)} patents from USPTO for query: {query}")

        except Exception as e:
            logger.error(f"USPTO fetch error: {e}")

        return patents

    async def fetch_clinical_trials(self, condition: str, intervention: str = None, max_results: int = 50) -> List[Dict]:
        """
        Fetch clinical trials from ClinicalTrials.gov API v2

        Args:
            condition: Disease/condition to search for
            intervention: Drug/intervention to search for
            max_results: Maximum number of results

        Returns:
            List of trial dictionaries
        """
        trials = []

        try:
            url = "https://clinicaltrials.gov/api/v2/studies"
            params = {
                'format': 'json',
                'query.cond': condition,
                'pageSize': max_results
            }

            if intervention:
                params['query.intr'] = intervention

            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()

                    for study in data.get('studies', []):
                        protocol = study.get('protocolSection', {})
                        identification = protocol.get('identificationModule', {})
                        status_module = protocol.get('statusModule', {})
                        design = protocol.get('designModule', {})
                        conditions_module = protocol.get('conditionsModule', {})

                        trial = {
                            'source': 'clinicaltrials',
                            'id': f"ct_{identification.get('nctId', '')}",
                            'nct_id': identification.get('nctId'),
                            'title': identification.get('briefTitle'),
                            'official_title': identification.get('officialTitle'),
                            'status': status_module.get('overallStatus'),
                            'phase': design.get('phases', []),
                            'study_type': design.get('studyType'),
                            'conditions': conditions_module.get('conditions', []),
                            'enrollment': design.get('enrollmentInfo', {}).get('count'),
                            'start_date': status_module.get('startDateStruct', {}).get('date'),
                            'completion_date': status_module.get('completionDateStruct', {}).get('date'),
                            'sponsors': self._extract_sponsors(protocol),
                            'interventions': self._extract_interventions(protocol)
                        }
                        trials.append(trial)

            logger.info(f"Fetched {len(trials)} clinical trials for condition: {condition}")

        except Exception as e:
            logger.error(f"ClinicalTrials.gov fetch error: {e}")

        return trials

    def _extract_sponsors(self, protocol: Dict) -> List[str]:
        """Extract sponsor information from protocol"""
        sponsors = []
        sponsors_module = protocol.get('sponsorCollaboratorsModule', {})

        lead_sponsor = sponsors_module.get('leadSponsor', {})
        if lead_sponsor.get('name'):
            sponsors.append(lead_sponsor['name'])

        for collab in sponsors_module.get('collaborators', []):
            if collab.get('name'):
                sponsors.append(collab['name'])

        return sponsors

    def _extract_interventions(self, protocol: Dict) -> List[Dict]:
        """Extract intervention information from protocol"""
        interventions = []
        arms_module = protocol.get('armsInterventionsModule', {})

        for intervention in arms_module.get('interventions', []):
            interventions.append({
                'type': intervention.get('type'),
                'name': intervention.get('name'),
                'description': intervention.get('description')
            })

        return interventions

    async def fetch_preprints(self, subject: str = 'pharmacology') -> List[Dict]:
        """
        Fetch preprints from bioRxiv and chemRxiv RSS feeds

        Args:
            subject: Subject area for bioRxiv

        Returns:
            List of preprint dictionaries
        """
        preprints = []

        try:
            feeds = [
                f"https://connect.biorxiv.org/biorxiv_xml.php?subject={subject}",
                "https://chemrxiv.org/engage/chemrxiv/feed"
            ]

            for feed_url in feeds:
                feed = feedparser.parse(feed_url)

                for entry in feed.entries[:20]:  # Get latest 20 entries
                    preprint = {
                        'source': 'preprint',
                        'id': f"preprint_{entry.get('id', '').split('/')[-1]}",
                        'title': entry.get('title', ''),
                        'abstract': entry.get('summary', ''),
                        'authors': entry.get('author', '').split(', ') if entry.get('author') else [],
                        'link': entry.get('link', ''),
                        'date': entry.get('published', ''),
                        'doi': entry.get('doi', entry.get('id', ''))
                    }
                    preprints.append(preprint)

            logger.info(f"Fetched {len(preprints)} preprints")

        except Exception as e:
            logger.error(f"Preprint fetch error: {e}")

        return preprints

    async def fetch_chembl_compounds(self, target: str, max_results: int = 50) -> List[Dict]:
        """
        Fetch bioactive compounds from ChEMBL

        Args:
            target: Target name to search for
            max_results: Maximum number of compounds

        Returns:
            List of compound dictionaries
        """
        compounds = []

        try:
            # Step 1: Search for target
            search_url = f"https://www.ebi.ac.uk/chembl/api/data/target/search?q={quote(target)}&format=json"

            async with self.session.get(search_url) as response:
                if response.status == 200:
                    data = await response.json()
                    targets = data.get('targets', [])

                    if targets:
                        target_chembl_id = targets[0]['target_chembl_id']

                        # Step 2: Get compounds for target
                        compounds_url = f"https://www.ebi.ac.uk/chembl/api/data/activity"
                        params = {
                            'target_chembl_id': target_chembl_id,
                            'format': 'json',
                            'limit': max_results,
                            'only': 'molecule_chembl_id,canonical_smiles,standard_type,standard_value,standard_units'
                        }

                        async with self.session.get(compounds_url, params=params) as comp_response:
                            if comp_response.status == 200:
                                comp_data = await comp_response.json()

                                for activity in comp_data.get('activities', []):
                                    compound = {
                                        'source': 'chembl',
                                        'id': f"chembl_{activity.get('molecule_chembl_id', '')}",
                                        'chembl_id': activity.get('molecule_chembl_id'),
                                        'smiles': activity.get('canonical_smiles'),
                                        'target': target,
                                        'target_chembl_id': target_chembl_id,
                                        'activity_type': activity.get('standard_type'),
                                        'activity_value': activity.get('standard_value'),
                                        'units': activity.get('standard_units')
                                    }
                                    compounds.append(compound)

            logger.info(f"Fetched {len(compounds)} compounds from ChEMBL for target: {target}")

        except Exception as e:
            logger.error(f"ChEMBL fetch error: {e}")

        return compounds

    async def fetch_pubchem_compounds(self, query: str, max_results: int = 50) -> List[Dict]:
        """
        Search PubChem for compounds

        Args:
            query: Compound name or identifier
            max_results: Maximum number of compounds

        Returns:
            List of compound dictionaries
        """
        compounds = []

        try:
            # Search by name
            search_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{quote(query)}/cids/JSON"

            async with self.session.get(search_url) as response:
                if response.status == 200:
                    data = await response.json()
                    cids = data.get('IdentifierList', {}).get('CID', [])[:max_results]

                    if cids:
                        # Get compound details
                        cids_str = ','.join(str(cid) for cid in cids)
                        details_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cids_str}/property/MolecularFormula,MolecularWeight,CanonicalSMILES,IUPACName/JSON"

                        async with self.session.get(details_url) as detail_response:
                            if detail_response.status == 200:
                                detail_data = await detail_response.json()

                                for prop in detail_data.get('PropertyTable', {}).get('Properties', []):
                                    compound = {
                                        'source': 'pubchem',
                                        'id': f"pubchem_{prop.get('CID')}",
                                        'cid': prop.get('CID'),
                                        'smiles': prop.get('CanonicalSMILES'),
                                        'formula': prop.get('MolecularFormula'),
                                        'weight': prop.get('MolecularWeight'),
                                        'iupac': prop.get('IUPACName'),
                                        'query': query
                                    }
                                    compounds.append(compound)

            logger.info(f"Fetched {len(compounds)} compounds from PubChem for query: {query}")

        except Exception as e:
            logger.error(f"PubChem fetch error: {e}")

        return compounds

    async def fetch_all_sources(self, campaign_goals: Dict[str, Any]) -> Dict[str, List[Dict]]:
        """
        Fetch data from all available sources based on campaign goals

        Args:
            campaign_goals: Dictionary containing target, indication, keywords

        Returns:
            Dictionary with results from each source
        """
        target = campaign_goals.get('target', '')
        indication = campaign_goals.get('indication', '')
        keywords = campaign_goals.get('keywords', [])

        # Build search queries
        pubmed_query = f"{target} {indication} " + ' '.join(keywords)
        patent_query = f"{target} inhibitor pharmaceutical"

        # Fetch from all sources in parallel
        tasks = [
            self.fetch_pubmed_papers(pubmed_query),
            self.fetch_patents(patent_query),
            self.fetch_clinical_trials(indication, target),
            self.fetch_preprints(),
            self.fetch_chembl_compounds(target),
            self.fetch_pubchem_compounds(target)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Organize results
        all_data = {
            'pubmed': results[0] if not isinstance(results[0], Exception) else [],
            'patents': results[1] if not isinstance(results[1], Exception) else [],
            'clinical_trials': results[2] if not isinstance(results[2], Exception) else [],
            'preprints': results[3] if not isinstance(results[3], Exception) else [],
            'chembl': results[4] if not isinstance(results[4], Exception) else [],
            'pubchem': results[5] if not isinstance(results[5], Exception) else []
        }

        # Log summary
        for source, data in all_data.items():
            if isinstance(data, list):
                logger.info(f"{source}: {len(data)} items")

        return all_data


# Test function
async def test_api_client():
    """Test the API client with real queries"""
    async with LiteratureAPIClient() as client:
        # Test PubMed
        papers = await client.fetch_pubmed_papers("KRAS G12C inhibitor", max_results=5)
        print(f"\nPubMed Papers: {len(papers)}")
        if papers:
            print(f"First paper: {papers[0].get('title', 'No title')}")

        # Test Patents
        patents = await client.fetch_patents("KRAS inhibitor", max_results=5)
        print(f"\nUSPTO Patents: {len(patents)}")
        if patents:
            print(f"First patent: {patents[0].get('title', 'No title')}")

        # Test Clinical Trials
        trials = await client.fetch_clinical_trials("lung cancer", "KRAS G12C", max_results=5)
        print(f"\nClinical Trials: {len(trials)}")
        if trials:
            print(f"First trial: {trials[0].get('title', 'No title')}")

        # Test all sources
        campaign_goals = {
            'target': 'KRAS G12C',
            'indication': 'NSCLC',
            'keywords': ['resistance', 'combination']
        }
        all_results = await client.fetch_all_sources(campaign_goals)

        print("\nAll Sources Summary:")
        for source, data in all_results.items():
            print(f"  {source}: {len(data)} items")


if __name__ == "__main__":
    # Run test
    asyncio.run(test_api_client())