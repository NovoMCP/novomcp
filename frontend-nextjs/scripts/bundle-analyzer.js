/**
 * Bundle Size Analyzer
 * Analyzes and reports on bundle sizes to ensure <200KB initial load
 */

const fs = require('fs');
const path = require('path');
const gzipSize = require('gzip-size');
const chalk = require('chalk');

const BUILD_DIR = path.join(__dirname, '../.next');
const TARGET_SIZE = 200 * 1024; // 200KB

async function analyzeBundles() {
  console.log(chalk.blue('\n📊 Analyzing bundle sizes...\n'));

  const results = {
    total: 0,
    files: [],
    warnings: [],
    errors: []
  };

  // Find all JS files in build directory
  const findJSFiles = (dir) => {
    const files = [];
    
    if (!fs.existsSync(dir)) {
      console.log(chalk.yellow('Build directory not found. Run "npm run build" first.'));
      return files;
    }

    const items = fs.readdirSync(dir);
    
    for (const item of items) {
      const fullPath = path.join(dir, item);
      const stat = fs.statSync(fullPath);
      
      if (stat.isDirectory()) {
        files.push(...findJSFiles(fullPath));
      } else if (item.endsWith('.js')) {
        files.push(fullPath);
      }
    }
    
    return files;
  };

  const jsFiles = findJSFiles(path.join(BUILD_DIR, 'static'));

  // Analyze each file
  for (const file of jsFiles) {
    const content = fs.readFileSync(file);
    const size = content.length;
    const gzipped = await gzipSize(content);
    
    const fileInfo = {
      path: path.relative(BUILD_DIR, file),
      size,
      gzipped,
      sizeKB: (size / 1024).toFixed(2),
      gzippedKB: (gzipped / 1024).toFixed(2)
    };
    
    results.files.push(fileInfo);
    results.total += gzipped;

    // Check for large files
    if (gzipped > 50 * 1024) { // 50KB warning threshold
      results.warnings.push(`${fileInfo.path} is ${fileInfo.gzippedKB}KB (gzipped)`);
    }
    
    if (gzipped > 100 * 1024) { // 100KB error threshold
      results.errors.push(`${fileInfo.path} is too large: ${fileInfo.gzippedKB}KB (gzipped)`);
    }
  }

  // Sort by size
  results.files.sort((a, b) => b.gzipped - a.gzipped);

  // Display results
  console.log(chalk.white('Top 10 Largest Files:'));
  console.log('─'.repeat(80));
  
  results.files.slice(0, 10).forEach(file => {
    const color = file.gzipped > 100 * 1024 ? chalk.red :
                  file.gzipped > 50 * 1024 ? chalk.yellow :
                  chalk.green;
    
    console.log(
      color(`${file.path.padEnd(50)} ${file.sizeKB.padStart(8)}KB → ${file.gzippedKB.padStart(8)}KB (gzipped)`)
    );
  });

  console.log('─'.repeat(80));

  // Summary
  const totalKB = (results.total / 1024).toFixed(2);
  const isUnderTarget = results.total < TARGET_SIZE;
  
  console.log('\n📈 Summary:');
  console.log(`   Total JS Size: ${chalk.bold(totalKB + 'KB')} (gzipped)`);
  console.log(`   Target Size: ${chalk.bold('200KB')}`);
  console.log(`   Status: ${isUnderTarget ? chalk.green('✓ PASS') : chalk.red('✗ FAIL')}`);

  if (results.warnings.length > 0) {
    console.log(chalk.yellow('\n⚠️  Warnings:'));
    results.warnings.forEach(warning => {
      console.log(chalk.yellow(`   - ${warning}`));
    });
  }

  if (results.errors.length > 0) {
    console.log(chalk.red('\n❌ Errors:'));
    results.errors.forEach(error => {
      console.log(chalk.red(`   - ${error}`));
    });
  }

  // Optimization suggestions
  if (!isUnderTarget) {
    console.log(chalk.cyan('\n💡 Optimization Suggestions:'));
    console.log('   1. Enable code splitting for large components');
    console.log('   2. Lazy load heavy dependencies');
    console.log('   3. Use dynamic imports for non-critical features');
    console.log('   4. Remove unused dependencies');
    console.log('   5. Minify and tree-shake your code');
  }

  // Generate report file
  const report = {
    timestamp: new Date().toISOString(),
    totalSize: results.total,
    totalSizeKB: totalKB,
    targetSize: TARGET_SIZE,
    targetSizeKB: (TARGET_SIZE / 1024).toFixed(2),
    passed: isUnderTarget,
    files: results.files.slice(0, 20),
    warnings: results.warnings,
    errors: results.errors
  };

  fs.writeFileSync(
    path.join(__dirname, '../bundle-report.json'),
    JSON.stringify(report, null, 2)
  );

  console.log(chalk.gray('\n📄 Full report saved to bundle-report.json\n'));

  // Exit with error if over target
  if (!isUnderTarget) {
    process.exit(1);
  }
}

// Check if required dependencies are installed
try {
  require('gzip-size');
  require('chalk');
} catch (error) {
  console.log('Installing required dependencies...');
  require('child_process').execSync('npm install --no-save gzip-size chalk', {
    stdio: 'inherit'
  });
}

// Run analyzer
analyzeBundles().catch(console.error);