window.dash_clientside = window.dash_clientside || {};

window.dash_clientside.clientside = {
    updateCountdown: function(n, last_update_timestamp, auto_refresh_enabled, settings_data) {
        // Se il polling è disattivato
        if (!auto_refresh_enabled) {
            return "Polling automatico disattivato";
        }

        // Mostra un messaggio di attesa
        if (!last_update_timestamp || !settings_data) {
            return "In attesa di polling...";
        }

        // Calcola il tempo rimanente
        const refresh_interval_seconds = settings_data.refresh_interval_ms / 1000;
        const elapsed_time = (Date.now() / 1000) - last_update_timestamp;
        let remaining_time = refresh_interval_seconds - elapsed_time;

        if (remaining_time < 0) {
            remaining_time = 0;
        }

        const total_seconds = Math.floor(remaining_time);
        
        // Formatta il tempo in HH:MM:SS
        const h = Math.floor(total_seconds / 3600);
        const m = Math.floor((total_seconds % 3600) / 60);
        const s = total_seconds % 60;

        // Funzione per aggiungere lo zero davanti quando necessario
        const pad = (num) => String(num).padStart(2, '0');

        let time_str = "";
        if (h > 0) {
            time_str = `${pad(h)}:${pad(m)}:${pad(s)}`;
        } else {
            time_str = `${pad(m)}:${pad(s)}`;
        }
            
        return `Polling tra: ${time_str}`;
    }
}