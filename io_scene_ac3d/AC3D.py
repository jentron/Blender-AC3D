import bpy, os, shutil, re
from math import radians, degrees
from mathutils import Vector, Matrix

DEBUG = False

def TRACE(message):
    if DEBUG:
        print(message)

# ------------------------------------------------------------------------------
class Object:
    '''
    Base class for an AC3D object.
    '''

    def __init__(    self,
                                name,
                                ob_type,
                                bl_obj,
                                export_config,
                                local_transform ):
        '''
        Create a AC3D object from a blender object and it's children
        
        @param name            The name of the object
        @param ob_type    The type of the object (world, poly, group, light)
        @param bl_obj    The according blender object
        @param export_config    Settings for export TODO move to export method?
        '''

        self.export_config = export_config
        self.name = name.replace('"','') # quotes not allowed...
        self.type = ob_type
        self.bl_obj = bl_obj
        self.hidden = False
        self.data = ''                # custom data (eg. description)
        self.url = ''                    # custom url (use for whatever you want but never ever
                                                    #             put spaces into it)
        
        if bl_obj:
            self.hidden = bl_obj.hide
            self.matrix_world = bl_obj.matrix_world
            localMatrix = bl_obj.matrix_local   # bl_obj.matrix_parent_inverse * bl_obj.matrix_local
            self.location = localMatrix.to_translation() #bl_obj.location#
            self.rotation = localMatrix.to_3x3()
        else:
            self.location = None
            self.rotation = None

        self.children = []
        self.parent = None

    def addChild( self,
                                child ):
        if not isinstance(child, Object):
            raise Exception('addChild: can only add children derived from Object')
        child.parent = self
        self.children.append(child)
        
    def _parse( self, ac_mats, str_pre ):
        '''
        Override to process the blender mesh and add materials to ac_mats if
        needed
        '''
        pass
    
    def parse( self, ac_mats, str_pre = '' ):
        TRACE("{0}+-({1}) {2}".format(str_pre, self.type, self.name))
        
        self._parse(ac_mats, str_pre)
        
        for child in self.children:
            child.parse(ac_mats, str_pre + ' ')

    def _write( self, strm ):
        pass

    def write( self, strm ):
        strm.write('OBJECT {0}\nname "{1}"\n'.format(self.type, self.name))
        
        if len(self.data):
            strm.write('data {0}\n'.format(len(self.data)))
            strm.write('{0}\n'.format(self.data))
            
        if len(self.url):
            strm.write('url {0}\n'.format(self.url))

        if self.hidden:
            strm.write('hidden\n')

        if self.location and self.export_config.export_rot:
            # position relative to parent
            location = self.location
            if any(c != 0 for c in location):
                x = '{0:.7f}'.format(location[0]).rstrip('0').rstrip('.')
                y = '{0:.7f}'.format(location[1]).rstrip('0').rstrip('.')
                z = '{0:.7f}'.format(location[2]).rstrip('0').rstrip('.')
                strm.write('loc {0:s} {1:s} {2:s}\n'.format(x,y,z))

        if self.rotation and self.export_config.export_rot:
            # rotation/scale relative to parent
            exportMatrix = self.rotation.to_3x3()
            if exportMatrix != Matrix().to_3x3():
                strm.write('rot {0:.7f} {1:.7f} {2:.7f} {3:.7f} {4:.7f} {5:.7f} {6:.7f} {7:.7f} {8:.7f}\n'.format(exportMatrix[0][0], exportMatrix[1][0], exportMatrix[2][0], exportMatrix[0][1], exportMatrix[1][1], exportMatrix[2][1], exportMatrix[0][2], exportMatrix[1][2], exportMatrix[2][2]))

        if self.type == 'world' and self.export_config.export_rot:
            exportMatrix = self.export_config.global_matrix
            if exportMatrix != Matrix().to_3x3():
                strm.write('rot {0:.7f} {1:.7f} {2:.7f} {3:.7f} {4:.7f} {5:.7f} {6:.7f} {7:.7f} {8:.7f}\n'.format(exportMatrix[0][0], exportMatrix[1][0], exportMatrix[2][0], exportMatrix[0][1], exportMatrix[1][1], exportMatrix[2][1], exportMatrix[0][2], exportMatrix[1][2], exportMatrix[2][2]))

        self._write(strm)
        strm.write('kids {0}\n'.format(len(self.children)))
                
        for child in self.children:
            child.write(strm)

# ------------------------------------------------------------------------------
class World (Object):
    '''
    Normally the root element is a world object
    '''
    def __init__( self,
                                name,
                                export_config,
                                local_transform = Matrix() ):
        Object.__init__(self, name, 'world', None, export_config, local_transform)

# ------------------------------------------------------------------------------
class Poly (Object):
    '''
    A polygon mesh
    '''
    def __init__( self,
                                name,
                                bl_obj,
                                export_config,
                                local_transform = Matrix() ):
        Object.__init__(self, name, 'poly', bl_obj, export_config, local_transform)
        
        self.crease = None
        self.vertices = []
        self.surfaces = []
        self.tex_name = ''    # texture name (filename of texture)
        self.tex_rep = [1,1]  # texture repeat
        self.ac_mats = {}     # Blender to AC3d index cross-reference
        self.ex_conf = export_config

    def _parse( self, ac_mats, str_pre ):

        if self.bl_obj:
            TRACE('{0}  ~ ({1}) {2}'.format( str_pre,
                                                                             self.bl_obj.type,
                                                                             self.bl_obj.data.name ))
            #if self.bl_obj.type == 'MESH':
            self._parseMesh(ac_mats)
    
    def _parseMesh( self, ac_mats ):
        mesh = self.bl_obj.to_mesh(self.export_config.context.scene, True, 'PREVIEW')
        orig_mesh = self.bl_obj.data
        if (orig_mesh):
            if (orig_mesh.name):
                self.data = orig_mesh.name.replace('"','') # quotes not allowed...
        self._parseMaterials(mesh, ac_mats)
        self._parseVertices(mesh)        
        self._parseFaces(mesh)        

        for mod in self.bl_obj.modifiers:
            if mod.type=='EDGE_SPLIT':
                self.crease = round(degrees(mod.split_angle), 3)
                break

        if not self.crease:
            if mesh.use_auto_smooth:
                self.crease = round(degrees(mesh.auto_smooth_angle), 3)
            else:
                self.crease = round(degrees(self.export_config.crease_angle), 3)

        #bpy.data.meshes.remove(mesh)
        
    def _parseMaterials( self, mesh, ac_mats ):
        '''
        Extract the materials from a blender mesh and create an id mapping from
        object material index to global AC3D material index
        '''
        mat_index = 0 # local material index
        for bl_mat in mesh.materials:
            if not bl_mat:
                continue
            ac_mat = Material(bl_mat.name, bl_mat, self.export_config)

            mat_exists = False
            for mat in ac_mats:
                if mat.same_as(ac_mat):
                    ac_mat = mat
                    mat_exists = True
                    break

            if not mat_exists:
                ac_mats.append(ac_mat)

            if not len(self.tex_name):
                for tex_slot in bl_mat.texture_slots:
                    if tex_slot:
                        old_tc = tex_slot.texture_coords
                        if tex_slot.texture_coords != 'UV':
                            tex_slot.texture_coords = 'UV'
                            #tex_slot.uv_layer = 
                        bl_tex = tex_slot.texture
                        if bl_tex.type == 'IMAGE':
                            bl_im = bl_tex.image
                        else:
                            bl_im = None
                        if(bl_im == None):
                            print("Texture has no image data (skipping): Tex name="+bl_tex.name+" Mat name="+bl_mat.name)
                            self.ex_conf.operator.report({'WARNING'}, 'AC3D Exporter: Texture "'+bl_tex.name+'" in material: "'+bl_mat.name+ '" contains no image data and was not exported.')
                            continue
                        tex_name = bpy.path.basename(bl_im.filepath)
                        export_tex = os.path.join(self.export_config.exportdir, tex_name)
                        if bl_im.packed_file:
                            splt = export_tex.rsplit('.', 1)[0]
                            export_tex = splt + '.png'
                            tex_name = bpy.path.basename(export_tex)
                        # TRACE('Exporting texture "{0}" to "{1}"'.format(bl_im.filepath, export_tex))
                        # TODO: Optionally over-write existing textures
                        if not bl_im.has_data:
                            # sometimes it has data, but its just not updated.
                            try:
                                bl_im.update()
                            except RuntimeError:
                                print ("")
                        if bl_im.has_data:
                            if not os.path.exists(export_tex):
                                if bl_im.packed_file:
                                    bl_im.file_format = 'PNG'
                                    orig_file_path = bl_im.filepath
                                    bl_im.filepath_raw = export_tex
                                    #print(bl_im.filepath)
                                    #print(bl_im.filepath_raw)
                                    bl_im.save()
                                    bl_im.filepath_raw = orig_file_path
                                    #base = os.path.splitext(export_tex)[0]
                                    #os.rename(export_tex, base + ".png")
                                    # bl_im.unpack('WRITE_ORIGINAL')
                                    # We cannot repack it after unpacking, as that will remove the newly saved image from the disk:
                                    # bl_im.pack(True)
                                else:
                                    abs_path = bpy.path.abspath(bl_im.filepath)
                                    if not os.path.exists(abs_path):
                                        TRACE('Warning: Texture doesn\'t exists: {0}'.format(bl_im.filepath))
                                    else:
                                        if not bl_im.is_dirty:
                                            shutil.copy(abs_path, export_tex)
                                        else:
                                            # To protect original texture, we actually save the modified texture only to the export location.
                                            # After exporting, the texture in Blender will point to the old location, but no longer be dirty.
                                            # Therefore users should be careful to save the image manually if they want the original to be overwritten.
                                            orig_file_path = bl_im.filepath
                                            bl_im.filepath_raw = export_tex
                                            bl_im.save()
                                            bl_im.filepath_raw = orig_file_path
                            # else:
                                # TRACE('File already exists "{0}"- not overwriting!'.format(tex_name))
                        else:
                            self.ex_conf.operator.report({'WARNING'}, 'AC3D Exporter: Texture "'+bl_tex.name+'" ('+tex_name+')'+' in material: "'+bl_mat.name+ '" contains no image and was not exported alongside model. (The .ac texture reference was exported though)')
                        
                        
                        self.tex_name = tex_name
                        tex_slot.texture_coords = old_tc
                        try:
                            self.tex_rep = [1, 1] #[tex_slot.texture.repeat_x, tex_slot.texture.repeat_y] this is not the same as blender texture repeat!
                        except:
                            print("Failed to export texrep")
                        break

            # Blender to AC3d index cross-reference
            # TRACE('Created Material {0} at index {1}'.format(ac_mats.index(ac_mat), mat_index))
            self.ac_mats[mat_index] = ac_mats.index(ac_mat)
            mat_index = mat_index + 1
    
    def _parseVertices( self, mesh ):
        '''
        Extract the vertices from a blender mesh
        '''
        transform = Matrix().to_4x4()
        transform.identity()
        if not self.export_config.export_rot:
            transform = self.export_config.global_matrix.to_4x4() * self.matrix_world
        self.vertices = [transform * v.co for v in mesh.vertices]
        
    def _parseFaces( self, mesh ):
        '''
        Extract the faces from a blender mesh
        '''
        uv_layer = None
        if len(mesh.uv_textures):
            uv_index = mesh.uv_textures.active_index
            if mesh.uv_textures[uv_index] != None:
                uv_layer = mesh.uv_layers.active.data[:]

        is_flipped = self.bl_obj.scale[0]\
                             * self.bl_obj.scale[1]\
                             * self.bl_obj.scale[2] < 0

        for face_idx in range(len(mesh.polygons)):
            poly = mesh.polygons[face_idx]
            
            uv_coords = []
            no_uv = False
            if(uv_layer):
                for loop_index in range(poly.loop_start, poly.loop_start + poly.loop_total):
                    #print("    Vertex: %d" % mesh.loops[loop_index].vertex_index)
                    #print("    UV: %r" % uv_layer[loop_index].uv)
                    uv_coords.append(uv_layer[loop_index].uv)
                    if(not uv_layer[loop_index].uv):
                        no_uv = True

            else:
                no_uv = True                

            if(no_uv):
                uv_coords = None

            surf = self.Surface(self.export_config, poly, self.ac_mats, mesh.show_double_sided, is_flipped, uv_coords, 0)
            self.surfaces.append(surf)

        if self.ex_conf.export_lines:
            # Standalone edges without faces.
            #
            # notice that an edge_key is actually a pair of indices to vertices
            #
            faceEdgeKeys = set([])
            for poly in mesh.polygons:
                for key in poly.edge_keys:
                    faceEdgeKeys.add(key)
            
            allEdgeKeys  = set( mesh.edge_keys )
            freeEdgeKeys = allEdgeKeys.difference( faceEdgeKeys )
            #print(str(len(faceEdgeKeys))+' '+str(len(allEdgeKeys))+' '+str(len(freeEdgeKeys)))

            freeEdges = set([])
            for f_edge in freeEdgeKeys:
                for b_edge in mesh.edges:
                    if b_edge.key == f_edge:
                        freeEdges.add(b_edge)

            for bl_edge in freeEdges:
                ac_edge = self.Surface(self.export_config, bl_edge, self.ac_mats, mesh.show_double_sided, is_flipped, None, 2)
                self.surfaces.append(ac_edge)

        
    def _write( self, strm ):

        strm.write('crease {0}\n'.format(self.crease))        

        if len(self.tex_name) > 0:
            strm.write('texture "{0}"\n'.format(self.tex_name))
            strm.write('texrep {0} {1}\n'.format(self.tex_rep[0], self.tex_rep[1]))

        if len(self.vertices):
            strm.write('numvert {0}\n'.format(len(self.vertices)))
            for vert in self.vertices:
                x = '{0:.5f}'.format(round(vert[0],5)).rstrip('0').rstrip('.')
                y = '{0:.5f}'.format(round(vert[1],5)).rstrip('0').rstrip('.') # with more than 5 digits the Blnder internal float representation becomes unreliable. Like 1 can become 0.999999 and stuff.
                z = '{0:.5f}'.format(round(vert[2],5)).rstrip('0').rstrip('.')
                strm.write('{0:s} {1:s} {2:s}\n'.format(x, y, z))

        if len(self.surfaces):
            strm.write('numsurf {0}\n'.format(len(self.surfaces)))
            for surf in self.surfaces:
                surf.write(strm)
    
    # ------------------------------                                                                                 
    class Surface:
        def __init__(    self,
                                    export_config,
                                    bl_face,
                                    ac_mats,
                                    is_two_sided,
                                    is_flipped,
                                    uv_coords,
                                    surf_type ):
            self.export_config = export_config
            self.mat = 0        # material index for this surface
            self.bl_face = bl_face
            self.uv_coords = uv_coords
            self.is_two_sided = is_two_sided
            self.is_flipped = is_flipped
            self.ac_surf_flags = self.SurfaceFlags(surf_type, False, True)

            self.parse_blender_face(bl_face, ac_mats)

        def write(self, ac_file):
            surf_flags = self.ac_surf_flags.getFlags()
            ac_file.write('SURF {0:#X}\n'.format(surf_flags))
            ac_file.write('mat {0}\n'.format(self.mat-self.export_config.mat_offset))
            ac_file.write('refs {0}\n'.format(len(self.bl_face.vertices)))

            r = range(len(self.bl_face.vertices))
            if self.is_flipped:
                r = reversed(r)

            if self.uv_coords:
                for n in r:
                    surf_ref = self.bl_face.vertices[n]
                    uv_ref = self.uv_coords[n]
                    u = '{0:.6f}'.format(round(uv_ref[0], 6)).rstrip('0').rstrip('.') # Blender seems to use doubles internally here, so more than 5 digits is okay.
                    v = '{0:.6f}'.format(round(uv_ref[1], 6)).rstrip('0').rstrip('.')
                    ac_file.write('{0} {1:s} {2:s}\n'.format(surf_ref, u, v))
            else:
                for n in r:
                    surf_ref = self.bl_face.vertices[n]
                    ac_file.write('{0} 0 0\n'.format(surf_ref))

        def parse_blender_face(self, bl_face, ac_mats):
            self.ac_surf_flags.twosided = self.is_two_sided

            try:
                if bl_face.material_index in ac_mats:
                    self.mat = ac_mats[bl_face.material_index]
            except:
                #is edge
                self.mat = 0
                if len(ac_mats) == 1:
                    # we only assign a material to a line if only 1 material is present in the mesh.
                    for val in ac_mats.values():
                        self.mat = val
            
            try:
                self.ac_surf_flags.smooth_shaded = bl_face.use_smooth
            except:
                #is edge
                self.ac_surf_flags.smooth_shaded = True
            if self.mat == 0:
                self.export_config.mat_offset = 0
        
        class SurfaceFlags:
            def __init__( self,
                                        surf_type,
                                        is_smooth,
                                        is_twosided    ):
                self.surf_type = surf_type
                self.smooth_shaded = is_smooth
                self.twosided = is_twosided

            def getFlags(self):
                n = self.surf_type & 0x0f
                if self.smooth_shaded:
                    n = n | 0x10
                if self.twosided:
                    n = n | 0x20
                return n

# ------------------------------------------------------------------------------
class Group (Object):
    '''
    An object group
    
    TODO maybe add an option to prevent exporting empty groups
    '''
    def __init__( self,
                                name,
                                bl_obj,
                                export_config,
                                local_transform = Matrix() ):
        Object.__init__(self, name, 'group', bl_obj, export_config, local_transform)

# ------------------------------------------------------------------------------
class Light (Object):
    '''
    An light group
    
    '''
    def __init__( self,
                                name,
                                bl_obj,
                                export_config,
                                local_transform = Matrix() ):
        Object.__init__(self, name, 'light', bl_obj, export_config, local_transform)
        if bl_obj.data:
            self.data = bl_obj.data.name.replace('"','')

# ------------------------------------------------------------------------------

# ------------------------------------------------------------------------------
class Material:
    '''
    Container class that defines the material properties of the .ac MATERIAL
    '''
    def __init__( self,
                                name = 'DefaultWhite',
                                bl_mat = None,
                                export_config = None ):
        self.name = name                                # string
        self.rgb = [1.0, 1.0, 1.0]            # [R,G,B]
        self.amb = [0.2, 0.2, 0.2]            # [R,G,B]
        self.emis = [0.0, 0.0, 0.0]            # [R,G,B]
        self.spec = [0.5, 0.5, 0.5]            # [R,G,B]
        self.shi = 10                                        # integer
        self.trans = 0                                    # float
        self.merge = False
        self.default = True
        self.export_config = export_config

        if bl_mat:
            # Blender:
            # ========
            # diffuse_intensity  : 0-1
            # diffuse_color      : 0-1 vector
            # mirror_color       : 0-1 vector
            # ambient            : 0-1
            # emit               : 0-2
            # specular_intensity : 0-1
            # specular_color     : 0-1 vector
            # specular_hardness  : 1-511
            # alpha              : 0-1
            #
            # AC3D:
            # ========
            # diffuse            : 0-1 vector
            # ambient            : 0-1 vector
            # emissive           : 0-1 vector
            # specular           : 0-1 vector
            # shininess          : 0-128
            # transparency       : 0-1
            #    
            self.default = False    
            self.name = re.sub('["]', '', bl_mat.name) # remove any " from the name.
            self.rgb = bl_mat.diffuse_intensity * bl_mat.diffuse_color
            if export_config.mircol_as_amb:
                self.amb = bl_mat.mirror_color
            elif export_config.amb_as_diff:
                self.amb = self.rgb
            else:
                self.amb = [bl_mat.ambient, bl_mat.ambient, bl_mat.ambient]
            if export_config.mircol_as_emis:
                self.emis = bl_mat.mirror_color# * bl_mat.emit   confusing if enabled, should be either mirror color or greyscale emissive
            else:
                self.emis = [bl_mat.emit/2, bl_mat.emit/2, bl_mat.emit/2]
            self.spec = bl_mat.specular_intensity * bl_mat.specular_color   
            self.merge = export_config.merge_materials

            acMin = 0.0
            acMax = 128.0
            blMin = 1.0
            blMax = 511.0
            acRange = (acMax - acMin)  
            blRange = (blMax - blMin)  
            self.shi = int(round((((float(bl_mat.specular_hardness) - blMin) * acRange) / blRange) + acMin, 0))

            if bl_mat.use_transparency:
                self.trans = 1.0 - bl_mat.alpha
            else:
                self.trans = 0.0

    def write( self, strm ):
        # MATERIAL %s rgb %f %f %f  amb %f %f %f  emis %f %f %f  spec %f %f %f  shi %d  trans %f
        if not (self.default and self.export_config.mat_offset == 1):
            strm.write('MATERIAL "{0}" rgb {1:.3f} {2:.3f} {3:.3f}  amb {4:.3f} {5:.3f} {6:.3f}  emis {7:.3f} {8:.3f} {9:.3f}  spec {10:.3f} {11:.3f} {12:.3f}  shi {13} trans {14:.3f}\n'.format(
                            self.name,
                            round(self.rgb[0],3), round(self.rgb[1],3), round(self.rgb[2],3),
                            round(self.amb[0],3), round(self.amb[1],3), round(self.amb[2],3),
                            round(self.emis[0],3), round(self.emis[1],3), round(self.emis[2],3),
                            round(self.spec[0],3), round(self.spec[1],3), round(self.spec[2],3),
                            self.shi,
                            round(self.trans,3),
                            ))

    def same_as( self, rhs ):
        if self.default or rhs.default:
            # Do not compare with DefaultWhite, as we might not output it.
            return False
        if self.merge:
            return  self._feq(self.rgb[0], rhs.rgb[0]) and \
                        self._feq(self.rgb[1], rhs.rgb[1]) and \
                        self._feq(self.rgb[2], rhs.rgb[2]) and \
                        self._feq(self.amb[0], rhs.amb[0]) and \
                        self._feq(self.amb[1], rhs.amb[1]) and \
                        self._feq(self.amb[2], rhs.amb[2]) and \
                        self._feq(self.emis[0], rhs.emis[0]) and \
                        self._feq(self.emis[1], rhs.emis[1]) and \
                        self._feq(self.emis[2], rhs.emis[2]) and \
                        self._feq(self.spec[0], rhs.spec[0]) and \
                        self._feq(self.spec[1], rhs.spec[1]) and \
                        self._feq(self.spec[2], rhs.spec[2]) and \
                        self._feq(self.shi, rhs.shi) and \
                        self._feq(self.trans, rhs.trans)
        # when not merging we still have to check the values since the char " has been removed from names.
        # for example there might have been a name called fuselageMat and a name "fuselage"Mat. They will seem similar if not checking the values.
        return self._feq(self.rgb[0], rhs.rgb[0]) and \
                        self._feq(self.rgb[1], rhs.rgb[1]) and \
                        self._feq(self.rgb[2], rhs.rgb[2]) and \
                        self._feq(self.amb[0], rhs.amb[0]) and \
                        self._feq(self.amb[1], rhs.amb[1]) and \
                        self._feq(self.amb[2], rhs.amb[2]) and \
                        self._feq(self.emis[0], rhs.emis[0]) and \
                        self._feq(self.emis[1], rhs.emis[1]) and \
                        self._feq(self.emis[2], rhs.emis[2]) and \
                        self._feq(self.spec[0], rhs.spec[0]) and \
                        self._feq(self.spec[1], rhs.spec[1]) and \
                        self._feq(self.spec[2], rhs.spec[2]) and \
                        self._feq(self.shi, rhs.shi) and \
                        self._feq(self.trans, rhs.trans) and \
                        self.name == rhs.name
        
    def _feq(self, lhs, rhs):
        return abs(rhs - lhs) < 0.0001
