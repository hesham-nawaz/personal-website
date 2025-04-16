document.addEventListener('DOMContentLoaded', function() {
    console.log('DOM loaded, initializing multi-dimensional filter and sort functionality');
    
    // Get all elements
    const categoryButtons = document.querySelectorAll('.category-btn');
    const subjectButtons = document.querySelectorAll('.subject-btn');
    const learningItems = document.querySelectorAll('.learning-item');
    const allCategoryButton = document.querySelector('.category-btn[data-category="all"]');
    const allSubjectButton = document.querySelector('.subject-btn[data-subject="all"]');
    const sortButton = document.getElementById('sort-toggle');
    const learningGrid = document.querySelector('.learning-grid');
    
    // Keep track of selected filters and sort order
    let selectedCategories = ['all'];
    let selectedSubjects = ['all'];
    let sortNewestFirst = true; // Default to newest first
    
    console.log('Found ' + categoryButtons.length + ' category buttons');
    console.log('Found ' + subjectButtons.length + ' subject buttons');
    console.log('Found ' + learningItems.length + ' learning items');
    
    // Function to apply filters and sorting
    function applyFiltersAndSort() {
        // Filter items based on selected categories and subjects
        learningItems.forEach(item => {
            const itemCategory = item.getAttribute('data-category');
            const itemSubjects = item.getAttribute('data-subject').split(' ');
            
            const matchesCategory = selectedCategories.includes('all') || selectedCategories.includes(itemCategory);
            
            // Check if any of the item's subjects match the selected subjects
            const matchesSubject = selectedSubjects.includes('all') || 
                                  itemSubjects.some(subject => selectedSubjects.includes(subject));
            
            // Item must match both category and subject filters
            if (matchesCategory && matchesSubject) {
                item.classList.remove('hidden');
            } else {
                item.classList.add('hidden');
            }
        });
        
        // Sort the visible items
        const visibleItems = Array.from(learningItems).filter(item => !item.classList.contains('hidden'));
        
        visibleItems.sort((a, b) => {
            const dateA = new Date(a.getAttribute('data-timestamp'));
            const dateB = new Date(b.getAttribute('data-timestamp'));
            
            return sortNewestFirst ? dateB - dateA : dateA - dateB;
        });
        
        // Reorder the items in the DOM
        visibleItems.forEach(item => {
            learningGrid.appendChild(item);
        });
        
        console.log('Applied filters and sorting:');
        console.log('- Categories: ' + selectedCategories.join(', '));
        console.log('- Subjects: ' + selectedSubjects.join(', '));
        console.log('- Sort order: ' + (sortNewestFirst ? 'newest first' : 'oldest first'));
    }
    
    // Add click event listeners to category buttons
    categoryButtons.forEach(button => {
        button.addEventListener('click', function() {
            const category = this.getAttribute('data-category');
            console.log('Category button clicked: ' + category);
            
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
                    allCategoryButton.classList.remove('active');
                }
                
                // Toggle the clicked category
                if (selectedCategories.includes(category)) {
                    // If already selected, remove it
                    selectedCategories = selectedCategories.filter(cat => cat !== category);
                    this.classList.remove('active');
                    
                    // If no categories are selected, reactivate "All"
                    if (selectedCategories.length === 0) {
                        selectedCategories = ['all'];
                        allCategoryButton.classList.add('active');
                    }
                } else {
                    // If not already selected, add it
                    selectedCategories.push(category);
                    this.classList.add('active');
                }
            }
            
            // Apply filters and sorting
            applyFiltersAndSort();
        });
    });
    
    // Add click event listeners to subject buttons
    subjectButtons.forEach(button => {
        button.addEventListener('click', function() {
            const subject = this.getAttribute('data-subject');
            console.log('Subject button clicked: ' + subject);
            
            // Handle the "All" button specially
            if (subject === 'all') {
                // If "All" is clicked, deselect all other subjects
                selectedSubjects = ['all'];
                subjectButtons.forEach(btn => {
                    if (btn.getAttribute('data-subject') === 'all') {
                        btn.classList.add('active');
                    } else {
                        btn.classList.remove('active');
                    }
                });
            } else {
                // If any other subject is clicked
                
                // First, handle the "All" button
                if (selectedSubjects.includes('all')) {
                    // Remove "all" from selected subjects
                    selectedSubjects = selectedSubjects.filter(sub => sub !== 'all');
                    allSubjectButton.classList.remove('active');
                }
                
                // Toggle the clicked subject
                if (selectedSubjects.includes(subject)) {
                    // If already selected, remove it
                    selectedSubjects = selectedSubjects.filter(sub => sub !== subject);
                    this.classList.remove('active');
                    
                    // If no subjects are selected, reactivate "All"
                    if (selectedSubjects.length === 0) {
                        selectedSubjects = ['all'];
                        allSubjectButton.classList.add('active');
                    }
                } else {
                    // If not already selected, add it
                    selectedSubjects.push(subject);
                    this.classList.add('active');
                }
            }
            
            // Apply filters and sorting
            applyFiltersAndSort();
        });
    });
    
    // Add click event listener to sort button
    sortButton.addEventListener('click', function() {
        sortNewestFirst = !sortNewestFirst;
        
        // Update button text and icon
        if (sortNewestFirst) {
            this.innerHTML = '<i class="fas fa-sort-amount-down"></i> Newest First';
        } else {
            this.innerHTML = '<i class="fas fa-sort-amount-up"></i> Oldest First';
        }
        
        // Apply filters and sorting
        applyFiltersAndSort();
    });
    
    // Initialize with default filters and sorting
    applyFiltersAndSort();
}); 