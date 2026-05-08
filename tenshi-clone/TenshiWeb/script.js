document.addEventListener('DOMContentLoaded', () => {
    // Interactivity for buttons
    const gameBtn = document.getElementById('btn-game');
    const chatBtn = document.getElementById('btn-chat');
    
    gameBtn.addEventListener('click', () => {
        // Animation effect
        gameBtn.style.transform = 'scale(0.9)';
        setTimeout(() => {
            gameBtn.style.transform = '';
            window.location.href = 'https://github.com/exile-tenshi/Tenshi/raw/main/SecureVoiceApp/TenshiVoice.exe';
        }, 150);
    });

    chatBtn.addEventListener('click', () => {
        chatBtn.style.transform = 'scale(0.9)';
        setTimeout(() => {
            chatBtn.style.transform = '';
            window.location.href = "hub.html"; // Relative link for both local and prod
        }, 150);
    });

    // Dynamic Server Fetcher
    async function fetchServers() {
        const slist = document.getElementById('server-list');
        if (!slist) return;

        // --- SPEED OPTIMIZATION: Load from Cache first ---
        const cached = localStorage.getItem('tenshi_public_servers');
        if (cached) {
            renderServers(JSON.parse(cached));
        } else {
            // Initial skeleton state
            slist.innerHTML = `<div class="skeleton-card"></div><div class="skeleton-card"></div>`;
        }

        const IS_PROD = window.location.hostname.includes("tenshi.lol");
        const apiUrl = IS_PROD ? "https://api.tenshi.lol" : "http://127.0.0.1:8080";
        try {
            let res = await fetch(apiUrl, {
                method: 'POST',
                body: JSON.stringify({action: "GET_PUBLIC_SERVERS"}),
                headers: {'Content-Type': 'application/json'}
            });
            let data = await res.json();
            if (data.status === 'success') {
                localStorage.setItem('tenshi_public_servers', JSON.stringify(data.servers));
                renderServers(data.servers);
            }
        } catch(e) {
            console.error(e);
        }
    }

    function renderServers(servers) {
        const slist = document.getElementById('server-list');
        let html = '';
        let colors = ['red', 'blue', 'green', 'purple', 'orange'];
        
        // servers is now a sorted List from the backend
        servers.forEach((s, idx) => {
            let initial = s.name.charAt(0).toUpperCase();
            let color = colors[idx % colors.length];
            let promoClass = s.is_promoted ? "promoted-gold" : "";
            let promoBadge = s.is_promoted ? `<span class="promo-badge">PROMOTED</span>` : "";

            html += `<div class="server-card ${promoClass}">
                <div class="server-icon ${color}">${initial}</div>
                <div class="server-info">
                    <h4>${s.name} ${promoBadge}</h4>
                    <p>${s.online_count} Online • ${s.member_count} Members</p>
                </div>
                <button class="join-btn" onclick="window.location.href='hub.html?joinServer=${encodeURIComponent(s.name)}'">JOIN</button>
            </div>`;
        });
        if (html) slist.innerHTML = html;
    }
    fetchServers();

    // Simple scroll reveal
    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.style.opacity = 1;
                entry.target.style.transform = 'translateY(0)';
            }
        });
    });

    document.querySelectorAll('.glass-panel').forEach((el) => observer.observe(el));
});
