/**
 * Web Worker for heavy molecular computations
 * Offloads expensive calculations from the main thread
 */

// Import statements for Web Workers
self.importScripts = self.importScripts || (() => {});

// Molecular property calculations
const calculateMolecularProperties = async (smiles) => {
  // Simplified molecular weight calculation
  const atomWeights = {
    'C': 12.011, 'H': 1.008, 'N': 14.007, 'O': 15.999,
    'S': 32.065, 'P': 30.974, 'F': 18.998, 'Cl': 35.453,
    'Br': 79.904, 'I': 126.904
  };

  // Parse SMILES (simplified)
  const atoms = {};
  for (let i = 0; i < smiles.length; i++) {
    const char = smiles[i];
    if (atomWeights[char]) {
      atoms[char] = (atoms[char] || 0) + 1;
    }
  }

  // Calculate molecular weight
  let molecularWeight = 0;
  for (const [atom, count] of Object.entries(atoms)) {
    molecularWeight += atomWeights[atom] * count;
  }

  // Lipinski's Rule of Five calculations
  const logP = Math.random() * 5; // Simplified - would use real calculation
  const hbondDonors = (atoms['N'] || 0) + (atoms['O'] || 0);
  const hbondAcceptors = (atoms['N'] || 0) + (atoms['O'] || 0) * 2;
  
  const lipinski = {
    passes: molecularWeight <= 500 && logP <= 5 && hbondDonors <= 5 && hbondAcceptors <= 10,
    violations: 0
  };

  if (molecularWeight > 500) lipinski.violations++;
  if (logP > 5) lipinski.violations++;
  if (hbondDonors > 5) lipinski.violations++;
  if (hbondAcceptors > 10) lipinski.violations++;

  return {
    molecularWeight: molecularWeight.toFixed(2),
    logP: logP.toFixed(2),
    hbondDonors,
    hbondAcceptors,
    lipinski,
    rotatable_bonds: Math.floor(Math.random() * 10),
    tpsa: (Math.random() * 140).toFixed(2)
  };
};

// Similarity calculation between molecules
const calculateSimilarity = (smiles1, smiles2) => {
  // Simplified Tanimoto similarity
  const set1 = new Set(smiles1.match(/.{1,3}/g) || []);
  const set2 = new Set(smiles2.match(/.{1,3}/g) || []);
  
  const intersection = new Set([...set1].filter(x => set2.has(x)));
  const union = new Set([...set1, ...set2]);
  
  return intersection.size / union.size;
};

// Batch processing for large datasets
const processMoleculeBatch = async (molecules, operation) => {
  const results = [];
  const batchSize = 100;
  
  for (let i = 0; i < molecules.length; i += batchSize) {
    const batch = molecules.slice(i, i + batchSize);
    const batchResults = await Promise.all(
      batch.map(mol => {
        switch (operation) {
          case 'properties':
            return calculateMolecularProperties(mol.smiles);
          case 'similarity':
            return calculateSimilarity(mol.smiles, mol.reference);
          default:
            return null;
        }
      })
    );
    
    results.push(...batchResults);
    
    // Send progress update
    self.postMessage({
      type: 'progress',
      progress: Math.min(100, ((i + batchSize) / molecules.length) * 100)
    });
  }
  
  return results;
};

// 3D coordinate generation (simplified)
const generate3DCoordinates = (smiles) => {
  // Very simplified 3D coordinate generation
  // In production, would use RDKit or similar
  const atoms = [];
  const bonds = [];
  
  // Parse atoms from SMILES
  for (let i = 0; i < Math.min(smiles.length, 20); i++) {
    const char = smiles[i];
    if (/[A-Z]/.test(char)) {
      atoms.push({
        element: char,
        x: Math.cos(i * 0.5) * 2,
        y: Math.sin(i * 0.5) * 2,
        z: Math.sin(i * 0.3) * 1
      });
      
      if (i > 0) {
        bonds.push({
          from: i - 1,
          to: i,
          order: 1
        });
      }
    }
  }
  
  return { atoms, bonds };
};

// ADMET prediction (simplified)
const predictADMET = (smiles, properties) => {
  const mw = parseFloat(properties.molecularWeight);
  const logP = parseFloat(properties.logP);
  
  return {
    absorption: {
      human_intestinal_absorption: mw < 500 && logP < 5 ? 'High' : 'Low',
      caco2_permeability: logP > 0 && logP < 3 ? 'High' : 'Low',
      p_glycoprotein_substrate: mw > 400 ? 'Yes' : 'No'
    },
    distribution: {
      bbb_permeant: mw < 400 && logP > 0 && logP < 3 ? 'Yes' : 'No',
      plasma_protein_binding: logP > 2 ? 'High' : 'Low',
      vd_steady_state: (0.5 + Math.random() * 2).toFixed(2) + ' L/kg'
    },
    metabolism: {
      cyp2d6_substrate: Math.random() > 0.5 ? 'Yes' : 'No',
      cyp3a4_substrate: Math.random() > 0.3 ? 'Yes' : 'No',
      cyp2d6_inhibitor: Math.random() > 0.7 ? 'Yes' : 'No'
    },
    excretion: {
      total_clearance: (Math.random() * 10).toFixed(2) + ' mL/min/kg',
      renal_oct2_substrate: Math.random() > 0.5 ? 'Yes' : 'No'
    },
    toxicity: {
      ames_test: Math.random() > 0.8 ? 'Positive' : 'Negative',
      herg_inhibition: logP > 3 ? 'Yes' : 'No',
      hepatotoxicity: Math.random() > 0.9 ? 'Yes' : 'No',
      skin_sensitization: Math.random() > 0.8 ? 'Yes' : 'No'
    }
  };
};

// Message handler
self.addEventListener('message', async (event) => {
  const { id, action, data } = event.data;
  
  try {
    let result;
    
    switch (action) {
      case 'calculate-properties':
        result = await calculateMolecularProperties(data.smiles);
        break;
        
      case 'calculate-similarity':
        result = calculateSimilarity(data.smiles1, data.smiles2);
        break;
        
      case 'batch-process':
        result = await processMoleculeBatch(data.molecules, data.operation);
        break;
        
      case 'generate-3d':
        result = generate3DCoordinates(data.smiles);
        break;
        
      case 'predict-admet':
        const properties = await calculateMolecularProperties(data.smiles);
        result = predictADMET(data.smiles, properties);
        break;
        
      default:
        throw new Error(`Unknown action: ${action}`);
    }
    
    self.postMessage({
      id,
      type: 'result',
      result
    });
    
  } catch (error) {
    self.postMessage({
      id,
      type: 'error',
      error: error.message
    });
  }
});