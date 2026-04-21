import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go

def afficher_tableau_bord_global(resultats_sim):
    """
    Affiche les KPI globaux avec les intitulés exacts du fichier Excel.
    """
    st.header("1. Dimensionnement et Indicateurs Globaux")
    
    kpis = resultats_sim["kpis"]
    d = st.session_state["data"]
    
    # Récupération du prix depuis param_vehicules (ajuste l'index si besoin)
    try:
        # On prend la valeur de la première ligne pour le prix global
        prix_carb = float(d["param_vehicules"]["Prix carburant (€/L)"].iloc[0])
    except:
        prix_carb = 0.0

    # --- Ligne 1 : Transport ---
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Besoin Max Chauffeurs", f"{kpis['nb_chauffeurs_max_jour']} ETP")
    c2.metric("Total Tournées", f"{kpis['nb_tournees']}")
    c3.metric("Remplissage Moyen", f"{kpis['remplissage_moyen']:.1f} %")
    c4.metric("Distance Totale", f"{int(kpis['distance_totale'])} km")

    # --- Ligne 2 : Énergie et Coûts ---
    st.markdown("---")
    e1, e2, e3 = st.columns(3)
    
    # Utilisation des intitulés exacts pour les unités
    conso_l = kpis.get('consommation_totale', 0)
    e1.metric("Consommation (L)", f"{int(conso_l)} L")
    e2.metric("Coût carbone (kg)", f"{int(kpis.get('co2_total', 0))} kg CO2")
    
    cout_total = conso_l * prix_carb
    e3.metric("Coût carburant total", f"{int(cout_total)} €", delta=f"Base : {prix_carb} €/L")


def afficher_analyse_operationnelle(resultats_sim, df_vehicules, df_contenants):
    """
    Interface interactive pour explorer le détail des tournées.
    Inclut la Timeline (Gantt) et le plan de chargement (Bin Packing).
    """
    st.header("2. Analyse Opérationnelle Détaillée")
    
    jours_nom = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    chauffeurs_obj = resultats_sim["obj_chauffeurs"]
    
    # --- FILTRE 1 : LE JOUR ---
    jour_selected = st.selectbox("📅 Choisir un jour", jours_nom, key="sel_jour_op")
    
    # Filtrage des chauffeurs ayant travaillé ce jour-là
    chauffeurs_du_jour = [c for c in chauffeurs_obj if c.id.split('_')[1] == jour_selected]
    
    if not chauffeurs_du_jour:
        st.warning(f"Aucune activité planifiée pour le {jour_selected}.")
        return

    # --- FILTRE 2 : LE CHAUFFEUR ---
    # Création d'un label lisible pour l'utilisateur
    mapping_ch = {f"{c.id} ({c.tournees[0].vehicule_type})": c for c in chauffeurs_du_jour}
    ch_label_sel = st.selectbox("🚚 Choisir un chauffeur / véhicule", list(mapping_ch.keys()), key="sel_chauf_op")
    c_selected = mapping_ch[ch_label_sel]

    # --- AFFICHAGE DE LA TIMELINE (GANTT) ---
    st.subheader(f"Chronologie de la journée : {ch_label_sel}")
    
    df_gantt = []
    temps_cumule = 480  # Hypothèse de départ à 08:00 (480 min)
    
    for i, t in enumerate(c_selected.tournees):
        start_time = pd.Timestamp("2026-01-01") + pd.Timedelta(minutes=temps_cumule)
        end_time = start_time + pd.Timedelta(minutes=t.duree_totale)
        
        df_gantt.append(dict(
            Task="Tournées",
            Start=start_time,
            Finish=end_time,
            Tournee=f"Tournée {t.id}",
            Details=f"Charge: {t.remplissage_actuel:.1f} / {t.capacite_max:.1f} contenants"
        ))
        # Marge de battement entre deux tournées (manutention, pauses...)
        temps_cumule += t.duree_totale + 15 

    fig_timeline = px.timeline(
        df_gantt, 
        x_start="Start", 
        x_end="Finish", 
        y="Task", 
        color="Tournee",
        hover_data=["Details"],
        text="Tournee",
        color_discrete_sequence=px.colors.qualitative.Pastel
    )
    fig_timeline.update_layout(showlegend=False, height=200, margin=dict(t=10, b=10))
    fig_timeline.update_xaxes(tickformat="%H:%M")
    st.plotly_chart(fig_timeline, use_container_width=True)

    # --- MÉTRIQUES DU CHAUFFEUR ---
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Amplitude Travail", f"{int(c_selected.temps_travail_cumule)} min")
    m2.metric("Nombre de rotations", len(c_selected.tournees))
    
    # Remplissage Poids (basé sur ton Poids max chargement)
    rempl_poids = np.mean([t.poids_actuel/t.capacite_max for t in c_selected.tournees]) * 100
    m3.metric("Remplissage Poids", f"{rempl_poids:.1f} %")
    
    # Remplissage Surface (basé sur tes dimensions internes m2)
    try:
        rempl_surf = np.mean([t.surface_actuelle / t.surface_max for t in c_selected.tournees]) * 100
        m4.metric("Occupation Surface", f"{rempl_surf:.1f} %")
    except:
        m4.metric("Occupation Surface", "N/A")

    # --- FILTRE 3 : LA TOURNÉE (SÉLECTION POUR VISUEL) ---
    mapping_t = {f"Tournée {t.id} (Hub: {t.hub_depart})": t for t in c_selected.tournees}
    t_label_sel = st.selectbox("📦 Sélectionner une tournée pour voir le chargement", list(mapping_t.keys()), key="sel_rot_op")
    t_selected = mapping_t[t_label_sel]

    # --- AFFICHAGE DU BIN PACKING (Vue Camion) ---
    st.subheader("📦 Plan de chargement (Vue de dessus)")
    
    # Import local du moteur pour la fonction de visuel
    from modules.simul_flux import generer_visuel_bin_packing
    
    fig_bin = generer_visuel_bin_packing(t_selected, df_vehicules, df_contenants)
    
    col_text, col_plot = st.columns([1, 2])
    with col_text:
        st.markdown(f"""
        **Détails de l'emport :**
        - **Origine :** {t_selected.hub_depart}
        - **Itinéraire :** {' ➔ '.join(t_selected.itineraire)}
        - **Contenants :** {int(t_selected.remplissage_actuel)} unités
        
        ---
        **Guide de lecture :**
        - Chaque rectangle représente un chariot/contenant.
        - La couleur correspond à la **destination**.
        - **Passez la souris** sur un rectangle pour voir :
            1. Le type de contenant.
            2. La fonction support (quoi ?).
            3. Le site de livraison (où ?).
        """)
        
    with col_plot:
        st.plotly_chart(fig_bin, use_container_width=True)

    # --- TABLEAU DES JOBS ---
    with st.expander("Voir la liste détaillée des flux de cette tournée"):
        df_jobs_t = pd.DataFrame([{
            "Job ID": j.id,
            "Destination": j.destination,
            "Fonction Support": j.fonction_support,
            "Nature de contenant": j.type_contenant,
            "Quantité": j.quantite,
            "Poids (T)": round(j.poids_total, 3),
            "Surface (m2)": round(j.surface_totale, 2)
        } for j in t_selected.jobs])
        st.table(df_jobs_t)


def generer_graphique_gantt(tournees_df):
    """
    Fonction utilitaire pour créer une timeline globale si nécessaire.
    """
    fig = px.timeline(
        tournees_df, 
        x_start="Start", 
        x_end="Finish", 
        y="ID Tournée", 
        color="Véhicule",
        title="Vue d'ensemble des rotations"
    )
    fig.update_yaxes(autorange="reversed")
    return fig

def afficher_gantt_flotte_complete(resultats_sim):
    """
    Affiche une frise chronologique de tous les véhicules sur une seule page.
    Inspiré du module Biologie.
    """
    st.header("🕒 Planning de la Flotte")
    
    jour_sel = st.selectbox("Choisir le jour à visualiser", 
                            ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"])
    
    # On récupère tous les chauffeurs/véhicules ayant travaillé ce jour-là
    data_gantt = []
    for ch in resultats_sim["obj_chauffeurs"]:
        if ch.id.startswith(f"CH_{jour_sel}"):
            heure_base = pd.Timestamp("2024-01-01 07:00") # Début de journée théorique
            current_time = heure_base
            
            for i, t in enumerate(ch.tournees):
                start = current_time
                end = start + pd.Timedelta(minutes=t.duree_totale)
                
                data_gantt.append({
                    "Véhicule": f"Chauffeur {ch.id.split('_')[-1]} ({t.vehicule_type})",
                    "Tournée": t.id,
                    "Début": start,
                    "Fin": end,
                    "Remplissage": f"{int((t.remplissage_actuel/t.capacite_max)*100)}%"
                })
                # On ajoute 15 min de pause/déchargement entre deux tournées
                current_time = end + pd.Timedelta(minutes=15)

    if data_gantt:
        df_gantt = pd.DataFrame(data_gantt)
        fig = px.timeline(
            df_gantt, x_start="Début", x_end="Fin", y="Véhicule", 
            color="Véhicule", text="Remplissage",
            title=f"Occupation des véhicules le {jour_sel}"
        )
        fig.update_yaxes(autorange="reversed")
        fig.update_layout(xaxis_title="Heure de la journée", showlegend=False)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Aucune activité simulée pour ce jour.")

def afficher_resultats_complets(resultats_sim, df_vehicules, df_contenants):
    """
    Cette fonction est le point d'entrée principal. 
    Elle affiche les sections dans l'ordre souhaité.
    """
    
    # 1. La frise chronologique globale de toute la flotte (Nouvelle vue)
    # On l'appelle en premier pour avoir la vision globale direct
    afficher_gantt_flotte_complete(resultats_sim)
    
    st.divider() # Ligne de séparation
    
    # 2. Les indicateurs globaux (ETP, Distance, etc.)
    # Votre fonction existante
    afficher_tableau_bord_global(resultats_sim)
    
    st.divider() # Ligne de séparation
    
    # 3. L'analyse opérationnelle détaillée (Filtre par chauffeur + Camion)
    # Votre fonction existante (on lui passe les dataframes nécessaires)
    afficher_analyse_operationnelle(resultats_sim, df_vehicules, df_contenants)
