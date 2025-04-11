document.addEventListener('DOMContentLoaded', function() {
    console.log('DOM loaded, initializing multi-select filter functionality');
    
    // Get all category buttons and learning items
    const categoryButtons = document.querySelectorAll('.category-btn');
    const learningItems = document.querySelectorAll('.learning-item');
    const allButton = document.querySelector('.category-btn[data-category="all"]');
    
    // Keep track of selected categories
    let selectedCategories = ['all'];
    
    console.log('Found ' + categoryButtons.length + ' category buttons');
    console.log('Found ' + learningItems.length + ' learning items');
    
    // Add click event listeners to category buttons
    categoryButtons.forEach(button => {
        button.addEventListener('click', function() {
            const category = this.getAttribute('data-category');
            console.log('Button clicked: ' + category);
            
            // Handle the "All" button specially
            if (category === 'all') {
                // If "All" is clicked, deselect all other categories
                selectedCategories = ['all'];
                categoryButtons.forEach(btn => {
                    if (btn.getAttribute('data-category') === 'all') {
                        btn.classList.add('active');
                    } else {
                        btn.classList.remove('active');
                    }
                });
            } else {
                // If any other category is clicked
                
                // First, handle the "All" button
                if (selectedCategories.includes('all')) {
                    // Remove "all" from selected categories
                    selectedCategories = selectedCategories.filter(cat => cat !== 'all');
                    allButton.classList.remove('active');
                }
                
                // Toggle the clicked category
                if (selectedCategories.includes(category)) {
                    // If already selected, remove it
                    selectedCategories = selectedCategories.filter(cat => cat !== category);
                    this.classList.remove('active');
                    
                    // If no categories are selected, reactivate "All"
                    if (selectedCategories.length === 0) {
                        selectedCategories = ['all'];
                        allButton.classList.add('active');
                    }
                } else {
                    // If not already selected, add it
                    selectedCategories.push(category);
                    this.classList.add('active');
                }
            }
            
            console.log('Selected categories: ' + selectedCategories.join(', '));
            
            // Filter learning items based on selected categories
            learningItems.forEach(item => {
                const itemCategory = item.getAttribute('data-category');
                console.log('Checking item with category: ' + itemCategory);
                
                if (selectedCategories.includes('all') || selectedCategories.includes(itemCategory)) {
                    item.classList.remove('hidden');
                } else {
                    item.classList.add('hidden');
                }
            });
        });
    });
    
    // Initialize with "All" selected
    allButton.click();
}); 