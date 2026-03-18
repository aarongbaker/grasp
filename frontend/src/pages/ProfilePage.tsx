import { useCallback, useEffect, useRef, useState } from 'react';
import { useAuth } from '../context/AuthContext';
import { updateKitchen, updateDietaryDefaults, addEquipment, deleteEquipment, getProfile } from '../api/users';
import { Skeleton } from '../components/shared/Skeleton';
import { Button } from '../components/shared/Button';
import { Input } from '../components/shared/Input';
import { Select } from '../components/shared/Select';
import type { Equipment, EquipmentCategory } from '../types/api';
import styles from './ProfilePage.module.css';

const MAX_BURNERS = 10;
const MAX_RACKS = 6;

const EQUIPMENT_CATEGORIES: { value: EquipmentCategory; label: string }[] = [
  { value: 'precision', label: 'Precision' },
  { value: 'baking', label: 'Baking' },
  { value: 'prep', label: 'Prep' },
  { value: 'specialty', label: 'Specialty' },
];

export function ProfilePage() {
  const { user, userId, setUser } = useAuth();
  const [saving, setSaving] = useState(false);
  const saveTimer = useRef<ReturnType<typeof setTimeout>>();

  // Equipment add form
  const [showAddForm, setShowAddForm] = useState(false);
  const [newEquipName, setNewEquipName] = useState('');
  const [newEquipCategory, setNewEquipCategory] = useState<EquipmentCategory>('prep');
  const [newEquipTechniques, setNewEquipTechniques] = useState('');

  // Dietary input
  const [dietaryInput, setDietaryInput] = useState('');

  const showSaveIndicator = useCallback(() => {
    setSaving(true);
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => setSaving(false), 1500);
  }, []);

  // Clean up timer
  useEffect(() => () => { if (saveTimer.current) clearTimeout(saveTimer.current); }, []);

  const refreshUser = useCallback(async () => {
    if (!userId) return;
    const profile = await getProfile(userId);
    setUser(profile);
  }, [userId, setUser]);

  if (!user) {
    return (
      <div className={styles.page}>
        <Skeleton variant="heading" />
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-md)', marginTop: 'var(--space-xl)' }}>
          <Skeleton variant="card" count={3} />
        </div>
      </div>
    );
  }

  const kc = user.kitchen_config;
  const burners = kc?.max_burners ?? 4;
  const racks = kc?.max_oven_racks ?? 2;
  const hasSecondOven = kc?.has_second_oven ?? false;
  const secondOvenRacks = kc?.max_second_oven_racks ?? 2;
  const dietaryDefaults = user.dietary_defaults ?? [];
  const equipment = user.equipment ?? [];

  // ─── Handlers ───

  async function handleBurnerClick(n: number) {
    if (!userId) return;
    try {
      const newCount = n === burners ? Math.max(1, n - 1) : n;
      await updateKitchen(userId, { max_burners: newCount });
      await refreshUser();
      showSaveIndicator();
    } catch (err) { console.error('Failed to update burners', err); }
  }

  async function handleRackChange(delta: number) {
    if (!userId) return;
    try {
      const newVal = Math.max(1, Math.min(MAX_RACKS, racks + delta));
      if (newVal === racks) return;
      await updateKitchen(userId, { max_oven_racks: newVal });
      await refreshUser();
      showSaveIndicator();
    } catch (err) { console.error('Failed to update racks', err); }
  }

  async function handleSecondOvenRackChange(delta: number) {
    if (!userId) return;
    try {
      const newVal = Math.max(1, Math.min(MAX_RACKS, secondOvenRacks + delta));
      if (newVal === secondOvenRacks) return;
      await updateKitchen(userId, { max_second_oven_racks: newVal });
      await refreshUser();
      showSaveIndicator();
    } catch (err) { console.error('Failed to update second oven racks', err); }
  }

  async function handleSecondOvenToggle() {
    if (!userId) return;
    try {
      await updateKitchen(userId, { has_second_oven: !hasSecondOven });
      await refreshUser();
      showSaveIndicator();
    } catch (err) { console.error('Failed to toggle second oven', err); }
  }

  async function handleAddDietary(value: string) {
    if (!userId || !value.trim()) return;
    try {
      const tag = value.trim().toLowerCase();
      if (dietaryDefaults.includes(tag)) return;
      const updated = [...dietaryDefaults, tag];
      await updateDietaryDefaults(userId, updated);
      await refreshUser();
      setDietaryInput('');
      showSaveIndicator();
    } catch (err) { console.error('Failed to update dietary defaults', err); }
  }

  async function handleRemoveDietary(tag: string) {
    if (!userId) return;
    try {
      const updated = dietaryDefaults.filter((d) => d !== tag);
      await updateDietaryDefaults(userId, updated);
      await refreshUser();
      showSaveIndicator();
    } catch (err) { console.error('Failed to remove dietary tag', err); }
  }

  async function handleAddEquipment() {
    if (!userId || !newEquipName.trim()) return;
    try {
      const techniques = newEquipTechniques
        .split(',')
        .map((t) => t.trim())
        .filter(Boolean);
      await addEquipment(userId, {
        name: newEquipName.trim(),
        category: newEquipCategory,
        unlocks_techniques: techniques,
      });
      await refreshUser();
      setNewEquipName('');
      setNewEquipCategory('prep');
      setNewEquipTechniques('');
      setShowAddForm(false);
      showSaveIndicator();
    } catch (err) { console.error('Failed to add equipment', err); }
  }

  async function handleDeleteEquipment(eq: Equipment) {
    if (!userId) return;
    try {
      await deleteEquipment(userId, eq.equipment_id);
      await refreshUser();
      showSaveIndicator();
    } catch (err) { console.error('Failed to delete equipment', err); }
  }

  // ─── Render ───

  return (
    <div className={styles.page}>
      <h1 className={styles.title}>Your Kitchen</h1>
      <p className={styles.subtitle}>
        Configure your setup so GRASP can schedule around what you actually have.
      </p>

      {/* Profile */}
      <div className={styles.profileRow}>
        <div className={styles.profileField}>
          <span className={styles.profileFieldLabel}>Name</span>
          <span className={styles.profileFieldValue}>{user.name}</span>
        </div>
        <div className={styles.profileField}>
          <span className={styles.profileFieldLabel}>Email</span>
          <span className={styles.profileFieldValue}>{user.email}</span>
        </div>
      </div>

      {/* Stovetop */}
      <div className={styles.section}>
        <div className={styles.sectionHeader}>
          <h2 className={styles.sectionTitle}>Stovetop</h2>
          <span className={styles.sectionHint}>Click burners to set your count</span>
        </div>

        <div className={styles.stovetop}>
          <div className={styles.burnerGrid}>
            {Array.from({ length: MAX_BURNERS }, (_, i) => {
              const n = i + 1;
              const active = n <= burners;
              return (
                <button
                  key={n}
                  className={`${styles.burner} ${active ? styles.burnerActive : ''}`}
                  onClick={() => handleBurnerClick(n)}
                  aria-label={`Burner ${n}${active ? ' (active)' : ''}`}
                  aria-pressed={active}
                  type="button"
                >
                  <div className={styles.burnerRing} />
                  <div className={styles.burnerDot} />
                </button>
              );
            })}
          </div>
          <div className={styles.burnerCount}>
            {burners}
            <div className={styles.burnerCountLabel}>
              burner{burners !== 1 ? 's' : ''} active
            </div>
          </div>
        </div>
      </div>

      {/* Ovens */}
      <div className={styles.section}>
        <div className={styles.sectionHeader}>
          <h2 className={styles.sectionTitle}>Ovens</h2>
          <div className={styles.toggleRow}>
            <span className={styles.toggleLabel}>Second oven</span>
            <button
              type="button"
              className={`${styles.toggle} ${hasSecondOven ? styles.toggleOn : ''}`}
              onClick={handleSecondOvenToggle}
              role="switch"
              aria-checked={hasSecondOven}
              aria-label="Toggle second oven"
            >
              <div className={styles.toggleKnob} />
            </button>
          </div>
        </div>

        <div className={styles.ovenRow}>
          {/* Primary oven */}
          <OvenVisual
            label="Primary Oven"
            racks={racks}
            maxRacks={MAX_RACKS}
            onRackChange={handleRackChange}
          />

          {/* Second oven */}
          <div className={!hasSecondOven ? styles.ovenDisabled : undefined}>
            <OvenVisual
              label="Second Oven"
              racks={hasSecondOven ? secondOvenRacks : 0}
              maxRacks={MAX_RACKS}
              onRackChange={hasSecondOven ? handleSecondOvenRackChange : undefined}
              disabled={!hasSecondOven}
            />
          </div>
        </div>
      </div>

      {/* Dietary Defaults */}
      <div className={styles.section}>
        <div className={styles.sectionHeader}>
          <h2 className={styles.sectionTitle}>Dietary Defaults</h2>
          <span className={styles.sectionHint}>Applied to every new session</span>
        </div>

        <div className={styles.dietaryContainer}>
          {dietaryDefaults.map((tag) => (
            <span key={tag} className={styles.dietaryTag}>
              {tag}
              <button
                type="button"
                className={styles.dietaryTagRemove}
                onClick={() => handleRemoveDietary(tag)}
                aria-label={`Remove ${tag}`}
              >
                &times;
              </button>
            </span>
          ))}
          <input
            className={styles.dietaryInput}
            type="text"
            placeholder="Add restriction..."
            value={dietaryInput}
            onChange={(e) => setDietaryInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                handleAddDietary(dietaryInput);
              }
            }}
          />
        </div>
      </div>

      {/* Equipment */}
      <div className={styles.section}>
        <div className={styles.sectionHeader}>
          <h2 className={styles.sectionTitle}>Equipment</h2>
          <span className={styles.sectionHint}>Unlocks techniques for recipe generation</span>
        </div>

        <div className={styles.equipmentGrid}>
          {equipment.map((eq) => (
            <div key={eq.equipment_id} className={styles.equipmentCard}>
              <div className={styles.equipmentInfo}>
                <span className={styles.equipmentName}>{eq.name}</span>
                <span className={styles.equipmentCategory}>{eq.category}</span>
                {eq.unlocks_techniques.length > 0 && (
                  <div className={styles.equipmentTechniques}>
                    {eq.unlocks_techniques.map((t) => (
                      <span key={t} className={styles.techniquePill}>{t}</span>
                    ))}
                  </div>
                )}
              </div>
              <button
                type="button"
                className={styles.removeBtn}
                onClick={() => handleDeleteEquipment(eq)}
                aria-label={`Remove ${eq.name}`}
              >
                &times;
              </button>
            </div>
          ))}

          {showAddForm ? (
            <div className={styles.addForm}>
              <Input
                placeholder="e.g. Sous Vide Circulator"
                value={newEquipName}
                onChange={(e) => setNewEquipName(e.target.value)}
                autoFocus
              />
              <div className={styles.addFormRow}>
                <Select
                  options={EQUIPMENT_CATEGORIES}
                  value={newEquipCategory}
                  onChange={(e) => setNewEquipCategory(e.target.value as EquipmentCategory)}
                />
              </div>
              <Input
                placeholder="Unlocks techniques (comma-separated)"
                value={newEquipTechniques}
                onChange={(e) => setNewEquipTechniques(e.target.value)}
              />
              <div className={styles.addFormActions}>
                <Button variant="ghost" size="sm" onClick={() => setShowAddForm(false)}>
                  Cancel
                </Button>
                <Button size="sm" onClick={handleAddEquipment} disabled={!newEquipName.trim()}>
                  Add
                </Button>
              </div>
            </div>
          ) : (
            <button
              type="button"
              className={styles.addEquipmentCard}
              onClick={() => setShowAddForm(true)}
            >
              + Add equipment
            </button>
          )}
        </div>
      </div>

      {/* Save indicator toast */}
      <div className={`${styles.saveIndicator} ${saving ? styles.saveIndicatorVisible : ''}`}>
        Saved
      </div>
    </div>
  );
}

/* ─── Oven subcomponent ─── */

function OvenVisual({
  label,
  racks,
  maxRacks,
  onRackChange,
  disabled,
}: {
  label: string;
  racks: number;
  maxRacks: number;
  onRackChange?: (delta: number) => void;
  disabled?: boolean;
}) {
  // Render rack slots — show all potential positions, highlight active ones
  const slots = Math.max(maxRacks, 3);

  return (
    <div className={styles.oven}>
      <div className={styles.ovenHeader}>
        <span className={styles.ovenTitle}>{label}</span>
      </div>
      <div className={styles.ovenCavity}>
        {Array.from({ length: slots }, (_, i) => (
          <div
            key={i}
            className={`${styles.rack} ${i < racks ? styles.rackActive : ''}`}
          />
        ))}
      </div>
      {onRackChange && !disabled && (
        <div className={styles.rackControls}>
          <button
            type="button"
            className={styles.rackBtn}
            onClick={() => onRackChange(-1)}
            disabled={racks <= 1}
            aria-label="Remove rack"
          >
            &minus;
          </button>
          <span className={styles.rackCount}>{racks}</span>
          <button
            type="button"
            className={styles.rackBtn}
            onClick={() => onRackChange(1)}
            disabled={racks >= maxRacks}
            aria-label="Add rack"
          >
            +
          </button>
        </div>
      )}
    </div>
  );
}
