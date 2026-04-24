import pandas as pd
import math
from datetime import time

# =================================================================
# 1. UTILITAIRES DE CONVERSION
# =================================================================

def to_decimal_minutes(t):
    """ Convertit un objet datetime.time en minutes depuis 00:00 """
    if isinstance(t, (pd.Timestamp, time)):
        return t.hour * 60 + t.minute
    return 0

# =================================================================
# 2. CLASSE POSTE CHAUFFEUR (Binôme Fixe)
# =================================================================

class PosteChauffeur:
    def __init__(self, id_poste, vehicule_type, site_initial, params_rh):
        self.id_poste = id_poste
        self.vehicule_type = vehicule_type
        self.stationnement_initial = site_initial
        
        # Position et Direction
        self.position_actuelle = site_initial
        self.couloir_actuel = None
        
        # États
        self.etat = 'INACTIF'
        self.job_en_cours = None
        self.temps_restant_etat = 0
        
        # Compteurs
        self.temps_service_total = 0   
        self.temps_conduite_cumule = 0 
        self.is_pause_faite = False
        
        # Paramètres RH (en minutes)
        self.amplitude_max = params_rh.get('v_duree', 450)
        self.duree_pause = params_rh.get('v_pause', 45)
        self.t_manoeuvre = 10 # Valeur par défaut, sera écrasée par df_vehicules
        
        # Traçabilité
        self.historique = []

    def enregistrer_evenement(self, minute_actuelle, activite, job=None, details=""):
        """ Enregistre un segment d'activité précis """
        destination = self.position_actuelle
        sj_id = "N/A"
        
        if job:
            sj_id = job.flux_id
            if activite in ['EN_TRAJET_PLEIN', 'EN_DECHARGEMENT']:
                destination = job.destination
            elif activite in ['EN_TRAJET_VIDE', 'EN_MANOEUVRE_QUAI']:
                # On manœuvre au site d'origine si on vient de finir un trajet vide
                destination = job.origin

        self.historique.append({
            "Poste": self.id_poste,
            "Type": self.vehicule_type,
            "Minute_Debut": minute_actuelle,
            "Heure_Debut": f"{int(minute_actuelle//60):02d}:{int(minute_actuelle%60):02d}",
            "Activite": activite,
            "Origine": self.position_actuelle,
            "Destination": destination,
            "SJ_ID": sj_id,
            "Details": details
        })

    def mettre_a_jour(self, pas_temps):
        if self.etat in ['INACTIF', 'FIN_POSTE']:
            return False

        self.temps_service_total += pas_temps
        if self.temps_restant_etat > 0:
            self.temps_restant_etat -= pas_temps
            if self.etat in ['EN_TRAJET_VIDE', 'EN_TRAJET_PLEIN']:
                self.temps_conduite_cumule += pas_temps
            
            if self.temps_restant_etat <= 0:
                self.temps_restant_etat = 0
                return True
            return False
        return True

    def verifier_besoin_pause(self):
        return not self.is_pause_faite and self.temps_service_total >= (self.amplitude_max / 2)

    def verifier_fin_service(self):
        return self.temps_service_total >= self.amplitude_max

    def est_disponible(self):
        return self.etat == 'DISPONIBLE' and self.temps_restant_etat == 0

# =================================================================
# 3. MOTEUR DE SÉQUENÇAGE
# =================================================================

def calculer_score_stress(job, temps_actuel):
    """ Calcule l'urgence d'un job avec facteur exponentiel """
    # On considère la durée totale du SJ (chargement + route + déchargement)
    duree_mission = job.poids_total 
    temps_restant = job.h_deadline_min - temps_actuel
    
    if temps_restant <= 0: return 99999 # Déjà en retard
    
    ratio = duree_mission / temps_restant
    # Le score explose quand ratio s'approche de 1.1
    return ratio * (1 / max(0.001, (1.1 - ratio)))

def trouver_meilleur_job(poste, jobs_dispos, matrice_duree):
    """ Applique la pyramide de décision pour choisir le job suivant """
    candidats = [j for j in jobs_dispos if j.v_type == poste.vehicule_type]
    if not candidats: return None
    
    # On trie les candidats par stress décroissant
    candidats.sort(key=lambda x: x.score_stress, reverse=True)
    
    # On regarde les N jobs les plus stressés (Fenêtre de choix)
    top_candidats = candidats[:5]
    
    # Priorité 1 : Même couloir + Origine = Position Actuelle
    for j in top_candidats:
        if j.couloir == poste.couloir_actuel and j.origin == poste.position_actuelle:
            return j
            
    # Priorité 2 : Hors couloir + Origine = Position Actuelle
    for j in top_candidats:
        if j.origin == poste.position_actuelle:
            return j

    # Priorité 3 : Même Groupe (Hub HSJ)
    # Note : Nécessite que job.origin_group soit défini
    for j in top_candidats:
        if hasattr(j, 'origin_group') and j.origin_group == "HUB_HSJ" and "HSJ_" in poste.position_actuelle:
            return j

    # Priorité 4 : Mixte Proximité / Stress
    scored_list = []
    for idx, j in enumerate(top_candidats):
        dist = matrice_duree.get(poste.position_actuelle, {}).get(j.origin, 999)
        # Score combiné : rang stress + (distance / 10)
        scored_list.append(((idx + (dist / 10)), j))
    
    scored_list.sort(key=lambda x: x[0])
    return scored_list[0][1]

def ordonnancer_journee(liste_sj, n_max_dict, df_vehicules, matrice_duree, params_logistique):
    """ Fonction principale de simulation séquentielle """
    
    pas = 5 # minutes
    h_prise_min = params_logistique['rh'].get('h_prise_min', time(6, 0))
    h_fin_max = params_logistique['rh'].get('h_fin_max', time(21, 0))
    
    heure_actuelle = to_decimal_minutes(h_prise_min)
    heure_limite = to_decimal_minutes(h_fin_max)
    
    # Initialisation des postes
    postes = []
    col_nom_v = df_vehicules.columns[0] # Première colonne pour le nom/type
    
    for v_type, n_veh in n_max_dict.items():
        # Extraction paramètres véhicule
        row_v = df_vehicules[df_vehicules['Type'] == v_type].iloc[0]
        site_depot = row_v['Stationnement initial']
        t_man = row_v['Temps de mise à quai - manœuvre, contact/admin (minutes)']
        
        for i in range(1, n_veh + 1):
            p = PosteChauffeur(f"{v_type}_{i:02d}", v_type, site_depot, params_logistique['rh'])
            p.t_manoeuvre = t_man
            postes.append(p)

    jobs_restants = list(liste_sj)
    
    # Boucle temporelle
    while heure_actuelle <= heure_limite:
        
        # A. MISE À JOUR DES ÉTATS ET TRANSITIONS
        for p in postes:
            if p.mettre_a_jour(pas):
                # Transition automatique après trajet/manœuvre/charge
                if p.etat == 'PRISE_POSTE':
                    p.etat = 'DISPONIBLE'
                    p.enregistrer_evenement(heure_actuelle, "DISPONIBLE", details="Prise de poste finie")
                
                elif p.etat == 'EN_TRAJET_VIDE':
                    p.etat = 'EN_MANOEUVRE_QUAI'
                    p.temps_restant_etat = p.t_manoeuvre
                    p.position_actuelle = p.job_en_cours.origin
                    p.enregistrer_evenement(heure_actuelle, "EN_MANOEUVRE_QUAI", p.job_en_cours, "Mise à quai Chargement")

                elif p.etat == 'EN_MANOEUVRE_QUAI':
                    # Si au site de départ -> Charger
                    if p.job_en_cours and p.position_actuelle == p.job_en_cours.origin:
                        p.etat = 'EN_CHARGEMENT'
                        p.temps_restant_etat = p.job_en_cours.temps_chargement # SJ doit avoir cet attr
                        p.enregistrer_evenement(heure_actuelle, "EN_CHARGEMENT", p.job_en_cours)
                    # Si au site d'arrivée -> Décharger
                    else:
                        p.etat = 'EN_DECHARGEMENT'
                        p.temps_restant_etat = p.job_en_cours.temps_dechargement
                        p.enregistrer_evenement(heure_actuelle, "EN_DECHARGEMENT", p.job_en_cours)

                elif p.etat == 'EN_CHARGEMENT':
                    p.etat = 'EN_TRAJET_PLEIN'
                    p.couloir_actuel = p.job_en_cours.couloir # On définit le couloir ici
                    dist = matrice_duree.get(p.job_en_cours.origin, {}).get(p.job_en_cours.destination, 30)
                    p.temps_restant_etat = dist
                    p.enregistrer_evenement(heure_actuelle, "EN_TRAJET_PLEIN", p.job_en_cours)

                elif p.etat == 'EN_TRAJET_PLEIN':
                    p.etat = 'EN_MANOEUVRE_QUAI'
                    p.temps_restant_etat = p.t_manoeuvre
                    p.position_actuelle = p.job_en_cours.destination
                    p.enregistrer_evenement(heure_actuelle, "EN_MANOEUVRE_QUAI", p.job_en_cours, "Mise à quai Déchargement")

                elif p.etat == 'EN_DECHARGEMENT':
                    # Vérification Retard
                    if heure_actuelle > p.job_en_cours.h_deadline_min:
                        # On pourrait logger le retard mais continuer la simu
                        p.enregistrer_evenement(heure_actuelle, "RETARD", p.job_en_cours, "Deadline dépassée")
                    
                    p.etat = 'DISPONIBLE'
                    p.job_en_cours = None
                    p.enregistrer_evenement(heure_actuelle, "DISPONIBLE", details="Fin de mission")

                elif p.etat == 'EN_PAUSE':
                    p.etat = 'DISPONIBLE'
                    p.enregistrer_evenement(heure_actuelle, "DISPONIBLE", details="Fin de pause")

        # B. AFFECTATION DES JOBS
        jobs_dispos = [j for j in jobs_restants if j.h_dispo_min <= heure_actuelle]
        for j in jobs_dispos:
            j.score_stress = calculer_score_stress(j, heure_actuelle)

        for p in postes:
            if p.etat == 'INACTIF':
                # Prise de poste si un job de son type est dispo
                if any(j.v_type == p.vehicule_type for j in jobs_dispos):
                    p.etat = 'PRISE_POSTE'
                    p.temps_restant_etat = 15 # Fixe
                    p.enregistrer_evenement(heure_actuelle, "PRISE_POSTE")
            
            elif p.est_disponible():
                # Gestion Pause ou Fin
                if p.verifier_fin_service():
                    if p.position_actuelle == p.stationnement_initial:
                        p.etat = 'FIN_POSTE'
                        p.enregistrer_evenement(heure_actuelle, "FIN_POSTE")
                    else:
                        dist_depot = matrice_duree.get(p.position_actuelle, {}).get(p.stationnement_initial, 30)
                        p.etat = 'EN_TRAJET_VIDE'
                        p.temps_restant_etat = dist_depot
                        p.enregistrer_evenement(heure_actuelle, "EN_TRAJET_VIDE", details="Retour Dépôt (Fin)")
                
                elif p.verifier_besoin_pause():
                    if p.position_actuelle == p.stationnement_initial:
                        p.etat = 'EN_PAUSE'
                        p.temps_restant_etat = p.duree_pause
                        p.is_pause_faite = True
                        p.enregistrer_evenement(heure_actuelle, "EN_PAUSE")
                    else:
                        dist_depot = matrice_duree.get(p.position_actuelle, {}).get(p.stationnement_initial, 30)
                        p.etat = 'EN_TRAJET_VIDE'
                        p.temps_restant_etat = dist_depot
                        p.enregistrer_evenement(heure_actuelle, "EN_TRAJET_VIDE", details="Retour Dépôt (Pause)")
                
                else:
                    # Recherche de mission
                    job = trouver_meilleur_job(p, jobs_dispos, matrice_duree)
                    if job:
                        p.job_en_cours = job
                        jobs_restants.remove(job)
                        jobs_dispos.remove(job)
                        dist_v = matrice_duree.get(p.position_actuelle, {}).get(job.origin, 0)
                        if dist_v > 0:
                            p.etat = 'EN_TRAJET_VIDE'
                            p.temps_restant_etat = dist_v
                            p.enregistrer_evenement(heure_actuelle, "EN_TRAJET_VIDE", job)
                        else:
                            p.etat = 'EN_MANOEUVRE_QUAI'
                            p.temps_restant_etat = p.t_manoeuvre
                            p.enregistrer_evenement(heure_actuelle, "EN_MANOEUVRE_QUAI", job)

        heure_actuelle += pas

    return postes, jobs_restants
