#!/usr/bin/env python

from collections import OrderedDict, deque
import inspect
import json
import os
import re
import shutil
import io
from subprocess import call
import sys, getopt
from pprint import pprint

from jinja2 import Environment, FileSystemLoader
import markdown
from markdown.extensions.toc import slugify

import apib_extra_parse_utils

def print_api_spec_title_to_extra_file(input_file_path, extra_sections_file_path):
    """Extracts the title of the API specification and writes it to the extra sections file.

    Arguments:
    input_file_path -- File with the API specification
    extra_sections_file_path -- File where we will write the extra sections
    """
    with open(input_file_path, 'rU') as input_file_path, open(extra_sections_file_path, 'w') as extra_sections_file:
        line = input_file_path.readline()
        while (line != "" and not line.startswith("# ")):
            line = input_file_path.readline()
    
        extra_sections_file.write( line )


def start_apib_section(line):
    """Tells if the line indicates the beginning of the apib section.

    Arguments:
    line -- Last read line from the FIWARE extended APIB file.
    """
    result = False

    group_regex = re.compile("^#*[ ]Group([ \w\W\-\_]*)$")
    resource_regex = re.compile("^#*[ ]([ \w\W\-\_]*) \[([ \w\W\-\_]*)\]$")
    direct_URI_regex = re.compile("^#*[ ]([ ]*[/][ \w\W\-\_]*)$")


    if (line.strip() == "# REST API" 
        or line.strip() == "## Data Structures"
        or group_regex.match(line)
        or resource_regex.match(line)
        or direct_URI_regex.match(line)
        ):

        result = True
    
    return result


def separate_extra_sections_and_api_blueprint(input_file_path, extra_sections_file_path, API_blueprint_file_path):
    """Divides a Fiware API specification into extra sections and its API blueprint.

    Arguments:
    input_file_path -- A Fiware API specification file.
    extra_sections_file_path -- Resulting file containing extra information about the API specification.
    API_blueprint_file_path -- Resulting file containing the API blueprint of the Fiware API.
    """
    print_api_spec_title_to_extra_file(input_file_path, extra_sections_file_path) 

    with open(input_file_path, 'rU') as input_file, open(extra_sections_file_path, 'a') as extra_sections_file, open(API_blueprint_file_path, 'w') as API_blueprint_file:
        metadata_section = True
        apib_part = False
        title_section = False
        parameters_section = False

        for line in input_file:
            copy = False

            if metadata_section and len(line.split(':')) == 1:
                metadata_section = False
                title_section = True
            
            if metadata_section:
                copy = False
            else:
                if title_section and line.startswith('##'):
                    title_section = False

                if title_section:
                    copy = False

                else:
                    if not apib_part:
                        apib_part = start_apib_section(line)
                        
                    if not apib_part:
                        copy = True
                    else:
                        copy = False

            if copy:
                extra_sections_file.write(line)
            else:
                line = line.replace('\t','    ')
                (line, parameters_section) = preprocess_apib_parameters_lines(line, parameters_section)
                API_blueprint_file.write(line)


def preprocess_apib_parameters_lines(line, defining_parameters):
    """Preprocess a given APIB line if it contains a parameter definition

    Arguments:
    line - line to be preprocessed
    defining_parameters - bool indicating whether we are in a parameters section (APIB) or not
    """
    if not defining_parameters:
        if line == '+ Parameters\n':
            defining_parameters = True
    else:
        if re.match(r"^[ \t]*[+|-][ ]([^ +-]*)[ ]*-?(.*)$", line) or re.match(r"^ *$", line):
            line = escape_parenthesis_in_parameter_description(line)
        else:
            defining_parameters = False

    return (line, defining_parameters)


def escape_parenthesis_in_parameter_description(parameter_definition):
    """Given an APIB parameter definition, escape the parenthesis in its description
    
    Arguments:
    line - string containing the parameter definition.
    """
    parameter_definition_list = parameter_definition.split(' - ', 1)
    if len(parameter_definition_list) > 1:
        parameter_header = parameter_definition_list[0]
        parameter_body = parameter_definition_list[1]

        parameter_body = parameter_body.replace('(', "&#40;")
        parameter_body = parameter_body.replace(')', "&#41;")

        return parameter_header + ' - ' + parameter_body
    else:
        return parameter_definition


def parser_api_blueprint(API_blueprint_file_path, API_blueprint_JSON_file_path):
    """Extracts from API Blueprint file the API specification and saves it to a JSON file

    Arguments:
    API_blueprint_file_path -- An API Blueprint definition file 
    API_blueprint_JSON_file_path -- Path to JSON file
    """

    call( ["drafter", API_blueprint_file_path, "--output", API_blueprint_JSON_file_path, "--format", "json", "--use-line-num"] )


def get_markdow_title_id(section_title):
    """Returns the HTML equivalent id from a section title
    
    Arguments: 
    section_title -- Section title
    """
    return section_title.replace(" ", "_").lower()


def get_heading_level(heading):
    """Returns the level of a given Markdown heading
    
    Arguments:
    heading -- Markdown title    
    """
    i = 0
    while( i < len(heading) and heading[i] == '#' ):
        i += 1

    return i


def create_json_section(section_markdown_title, section_body):
    """Creates a JSON
    
    Arguments:
    section_markdown_title -- Markdown title of the section
    section_body -- body of the subsection
    """
    section_title = section_markdown_title.lstrip('#').strip()

    section = {}
    section["id"] = get_markdow_title_id( section_title )
    section["name"] = section_title
    try:
        section["body"] = markdown.markdown( section_body.decode('utf-8'), extensions=['markdown.extensions.tables','markdown.extensions.fenced_code'] )
    except UnicodeDecodeError as ude:
        section["body"] = markdown.markdown( section_body, extensions=['markdown.extensions.tables','markdown.extensions.fenced_code'] )
    section["subsections"] = []

    return section


def get_subsection_body(file_descriptor):
    """Reads the given file until a Markdown header is found and returns the bytes read

    Arguments:
    file_descriptor -- Descriptor of the file being read"""
    body=''
    line = file_descriptor.readline()

    while line and not line.startswith('#'):
        body += line
        line = file_descriptor.readline()

    return (body, line)


def parse_metadata_subsections(file_descriptor, parent_section_JSON, last_read_line=None):
    """Generates a JSON tree of nested metadata sections

    Arguments:
    file_descriptor -- list of lines with the content of the file
    parent_section_JSON -- JSON object representing the current parent section
    last_read_line -- Last remaining read line 
    """
    
    if last_read_line is None:
        line = file_descriptor.readline()
    else:
        line = last_read_line

    # EOF case
    if not line:
        return line

    if line.startswith('#'):
        section_name = line
        (body, line) = get_subsection_body(file_descriptor)

        section_JSON = create_json_section(section_name, body)

        parent_section_JSON['subsections'].append(section_JSON)

        section_level = get_heading_level(section_name)
        next_section_level = get_heading_level(line)

        if section_level == next_section_level:   # Section sibling
           next_line = parse_metadata_subsections(file_descriptor, parent_section_JSON, last_read_line=line) 
        elif section_level < next_section_level:  # Section child
           next_line = parse_metadata_subsections(file_descriptor, section_JSON, last_read_line=line) 
        else:   # Not related to current section
            return line

        if not next_line :
            return next_line
        else:
            next_section_level = get_heading_level(next_line)

            if section_level == next_section_level:   # Section sibling
               next_line = parse_metadata_subsections(file_descriptor, parent_section_JSON, last_read_line=next_line) 
            else:   # Not related to current section
                return next_line


def parse_meta_data(file_path):
    """Parses API metadata and returns the result in a JSON object
    
    Arguments: 
    file_path -- File with extra sections
    """
    metadata = create_json_section("root", "")
  
    with open(file_path, 'rU') as file_:
        more = parse_metadata_subsections(file_, metadata)
        while more:
            more = parse_metadata_subsections(file_, metadata, more)

    return metadata


def generate_metadata_dictionary(metadata_section):
    """Generates a metadata section as a dictionary from a non-dictionary section
    
    Arguments:
    metadata_section -- Source metadata section
    """
    metadata_section_dict = {}
    metadata_section_dict['id'] = metadata_section['id']
    metadata_section_dict['name'] = metadata_section['name']
    metadata_section_dict['body'] = metadata_section['body']
    metadata_section_dict['subsections'] = OrderedDict()

    for subsection in metadata_section['subsections']:
        metadata_section_dict['subsections'][subsection['name']] = generate_metadata_dictionary(subsection)

    return metadata_section_dict


def add_metadata_to_json(metadata, JSON_file_path):
    """Adds metadata values to a json file
    
    Arguments: 
    metadata -- Metadata values in JSON format
    JSON_file_path -- Path to JSON file
    """
    json_content = ""

    with open(JSON_file_path, 'rU') as json_file:
        json_content = json.load(json_file)
        json_content['api_metadata'] = {}
        for metadataKey in metadata:
            json_content['api_metadata'][metadataKey] = metadata[metadataKey]

    #json_content['api_metadata_dict'] = generate_metadata_dictionary( metadata )

    with open(JSON_file_path, 'w') as json_file:
        json.dump(json_content, json_file, indent=4)


def add_is_pdf_metadata_to_json(is_PDF, JSON_file_path):
    """Specifies if FABRE are going to render a PDF or not
    
    Arguments: 
    is_PDF -- Boolean that indicates if FABRE should renderer the PDF template.
    JSON_file_path -- Path to JSON file
    """
    json_content = ""

    with open(JSON_file_path, 'rU') as json_file:
        json_content = json.load(json_file)
        json_content['is_PDF'] = is_PDF

    with open(JSON_file_path, 'w') as json_file:
        json.dump(json_content, json_file, indent=4)


def parser_json_descriptions(JSON_file_path):
    """Gets the descriptions of resources and actions and parses them as markdown. Saves the result in the same JSON file.
    
    Arguments: 
    JSON_file_path -- Path to JSON file
    """
    json_content = ""
    
    with open(JSON_file_path, 'rU') as json_file:
        json_content = json.load(json_file)
        for resource_group in json_content['resourceGroups']:
            resource_group['description'] = markdown.markdown( resource_group['description'], extensions=['markdown.extensions.tables'] )
            for resource in resource_group['resources']:
                resource['description'] = markdown.markdown( resource['description'], extensions=['markdown.extensions.tables'] )
                for action in resource['actions']:
                    action['description'] = markdown.markdown( action['description'], extensions=['markdown.extensions.tables'] )
    
    with open(JSON_file_path, 'w') as json_file:
        json.dump(json_content, json_file, indent=4)


def copy_static_files(template_dir_path, dst_dir_path):
    """Copies the static files used by the resulting rendered site
    
    Arguments:
    template_dir_path -- path to the template directory
    dst_dir_path -- destination directory
    """
    subdirectories = ['/css', '/js', '/img', '/font']

    for subdirectory in subdirectories:
        if os.path.exists(dst_dir_path + subdirectory):
            shutil.rmtree(dst_dir_path + subdirectory)
        shutil.copytree(template_dir_path + subdirectory, dst_dir_path + subdirectory)


def render_api_blueprint(template_file_path, context_file_path, dst_dir_path):
    """Renders an API Blueprint context file with a Jinja2 template.
    
    Arguments: 
    template_file_path -- The Jinja2 template path 
    context_file_path -- Path to the context file  
    dst_dir_path -- Path to save the compiled site
    """

    env = Environment(loader=FileSystemLoader(os.path.dirname(template_file_path)))
    template = env.get_template(os.path.basename(template_file_path))
    output = ""
    with open(context_file_path, "rU") as contextFile:
        output = template.render(json.load(contextFile))

    rendered_HTML_filename = os.path.splitext(os.path.basename(context_file_path))[0]
    rendered_HTML_path = os.path.join(dst_dir_path, rendered_HTML_filename + ".html")
    with open(rendered_HTML_path, 'w') as output_file:
        output_file.write(output.encode('utf-8'))
    copy_static_files(os.path.dirname(template_file_path), dst_dir_path)


def create_directory_if_not_exists(dir_path):
    """Creates a directory with the given path if it doesn't exists yet"""

    if not os.path.exists(dir_path):
        os.makedirs(dir_path)


def clear_directory(dir_path):
    """Removes all the files on a directory given its path"""
    
    for file in os.listdir(dir_path):
        file_path = os.path.join(dir_path, file)
        try:
            if os.path.isfile(file_path):
                os.unlink(file_path)
        except Exception, e:
            print e


def add_description_to_json_object_parameter_value(JSON_object, parameter_name, value_name, value_description):
    """"""
    value_object = None

    for object_parameter in JSON_object['parameters']:
        if object_parameter['name'] == parameter_name:
            for parameter_value in object_parameter['values']:
                if parameter_value['value'] == value_name:
                    value_object = parameter_value
                    
    if value_object != None:
        value_object['description'] = value_description


def add_description_to_json_parameter_value(JSON_file_path, resource_or_action_markdown_header, parameter_name, value_name, value_description):
    """"""
    json_content = ""

    wanted_object = extract_markdown_header_dict( resource_or_action_markdown_header)

    with open(JSON_file_path, 'rU') as json_file:
        json_content = json.load(json_file)

    found_object = None

    if 'method' in wanted_object:
        for resource_group in json_content['resourceGroups']:
            for resource in resource_group['resources']:
                for action in resource['actions']:
                    if( action['name'] == wanted_object['name'] and action['method'] == wanted_object['method'] and action['attributes']['uriTemplate'] == wanted_object['uriTemplate'] ):
                        found_object = action
                        break
    else:
        for resource_group in json_content['resourceGroups']:
            for resource in resource_group['resources']:
                if resource['name'] == wanted_object['name'] and resource['uriTemplate'] == wanted_object['uriTemplate']:
                    found_object = resource
                    break

    if found_object != None:
        add_description_to_json_object_parameter_value(found_object, parameter_name, value_name, value_description)
                
    with open(JSON_file_path, 'w') as json_file:
        json.dump(json_content, json_file, indent=4)


def parse_property_member_declaration(property_member_declaration_string):
  """ Utility to parse the declaration of a property member into custom JSON. Based on the MSON specification. """
  

  # Store MSON reserved words for the parsing below.
  # We are interested in the type attribute reserved keywords in order to know whether 
  # a property member is required or optional.
  reserved_keywords = {}
  reserved_keywords['type_attribute'] = ['required', 'optional', 'fixed', 'sample', 'default']

  if property_member_declaration_string == '': return {}

  # Parse the line in order to get the following fields:
  #  - property_name: The name given to the property
  #  - type_definition_list: The list with the technical definition of the property. Since this
  #    list is unordered, we will parse it later to find the needed keywords.
  #  - description: The text provided to describe the context of the property.
  regex_string = "^[ ]*[-|+][ ](?P<property_name>\w+)[ ]*(?:[[: ][\w, ]*]?[ ]*\((?P<type_definition_list>[\w\W ]+)\))?[ ]*(?:[-](?P<property_description>[ \w\W]+))?\Z"
  declaration_regex = re.compile(regex_string)

  declaration_match = declaration_regex.match(property_member_declaration_string)
  declaration_dict = declaration_match.groupdict()
  
  property_declaration={}
  property_declaration['name'] = declaration_dict['property_name']
  property_declaration['description'] = declaration_dict['property_description']
  property_declaration['subproperties'] = []
  property_declaration['values'] = []

  # Construct the type_definition field from the type_definition_list field retrieved in the
  # regular expression.
  property_declaration['required']=False      # Default value for the required attribute
  for type_specification_attribute in declaration_dict['type_definition_list'].split(','):
    # If the current element is not in the type_attributes reserved keywords list, it is
    # the property type specification.
    if type_specification_attribute.strip() not in reserved_keywords['type_attribute']:
      property_declaration['type'] = type_specification_attribute.strip()
    else:
      if type_specification_attribute.strip() == 'required': property_declaration['required']=True

  return property_declaration


def get_indentation(line):
    """Returns the indentation (number of spaces and tabs at the begining) of a given line"""
    i = 0
    while (i < len(line) and (line[i] == ' ' or line[i] == '\t')):
        i += 1
    return i


def parse_defined_data_structure_properties(properties_list, remaining_property_lines):
    """Parses the properties definitions of a given data structure given its body

    Arguments:
    properties_list - List where we'll insert new properties to
    remaining_property_lines - Property definition lines pending to be processed
    """
    last_member_indentation = -1

    while len(remaining_property_lines) > 0:
        property_member_declaration = remaining_property_lines[0]
        if property_member_declaration != '':
            # Retrieve the indentation of the current property definition.
            current_member_indentation = get_indentation(property_member_declaration)
            if last_member_indentation == -1:
                last_member_indentation = current_member_indentation
          
            # Process the new property as a child, parent or uncle of the last
            # one processed according to their relative line indentations.
            if current_member_indentation == last_member_indentation:
                parsed_attribute_definition = parse_property_member_declaration(property_member_declaration)
                remaining_property_lines.popleft()
                properties_list.append(parsed_attribute_definition)
            elif current_member_indentation > last_member_indentation:
                parse_defined_data_structure_properties(parsed_attribute_definition['subproperties'], remaining_property_lines)
            else:
                return
        else:
            remaining_property_lines.popleft()
    

def parse_defined_data_structures(data):
  """Retrieves data structures definition from JSON fragment and gives them back as Python dict"""
  data_structure_dict = {}

  try:
    if data["content"][0]["sections"][0]["class"] != u'blockDescription':
        raise ValueError('Unexpected section received.')
  except:
    return data_structure_dict


  for content in data["content"]:
    data_structure = {}
    data_structure_definition = []

    if content["sections"]!=[]:
      data_structure_content = content["sections"][0]["content"]
      parse_defined_data_structure_properties(data_structure_definition, deque(data_structure_content.split('\n')))

    data_structure_name = content["name"]["literal"]
    data_structure["attributes"] = data_structure_definition
    data_structure_dict[data_structure_name] = data_structure

  return data_structure_dict


def parser_json_data_structures(JSON_file_path):
    """Retrieves data structures definition from JSON file and writes them in an easier to access format"""
    
    json_content = ""

    with open(JSON_file_path, 'rU') as json_file:
        json_content = json.load(json_file)

    if len(json_content['content']) > 0:
        json_content['data_structures'] = parse_defined_data_structures(json_content['content'][0])
    else:
        json_content['data_structures'] = {}
    
    with open(JSON_file_path, 'w') as json_file:
        json.dump(json_content, json_file, indent=4)


def extract_markdown_header_dict(markdown_header):
    """Returns a dict with the elements of a given Markdown header (for resources or actions)"""
    markdown_header = markdown_header.lstrip('#').strip()
    
    p = re.compile("(.*) \[(\w*) (.*)\]")
    
    header_dict = {}
    if p.match( markdown_header ):
        header_groups = p.match(markdown_header).groups()

        header_dict['name'] = header_groups[0]
        header_dict['method'] = header_groups[1]
        header_dict['uriTemplate'] = header_groups[2]
    else:
        p = re.compile("(.*) \[(.*)\]")
        header_groups = p.match( markdown_header ).groups()

        header_dict['name'] = header_groups[0]
        header_dict['uriTemplate'] = header_groups[1]
        
    return header_dict


def add_custom_code_to_action_or_resource_json(JSON_file_path, action_markdown_line, new_key, new_value):
    """Finds an action or resource in the JSON file given its Markdown header line and adds a new key value to it"""
    
    json_content = ""

    wanted_object = extract_markdown_header_dict(action_markdown_line)

    with open(JSON_file_path, 'rU') as json_file:
        json_content = json.load(json_file)

    found_object = None

    if 'method' in wanted_object:
        for resource_group in json_content['resourceGroups']:
            for resource in resource_group['resources']:
                for action in resource['actions']:
                    if (action['name'] == wanted_object['name'] and action['method'] == wanted_object['method'] and action['attributes']['uriTemplate'] == wanted_object['uriTemplate']):
                        found_object = action
                        break
    else:
        for resource_group in json_content['resourceGroups']:
            for resource in resource_group['resources']:
                if (resource['name'] == wanted_object['name'] and resource['uriTemplate'] == wanted_object['uriTemplate']):
                    found_object = resource
                    break

    if found_object != None:
        found_object[new_key] = new_value
                
    with open(JSON_file_path, 'w') as json_file:
        json.dump(json_content, json_file, indent=4)


def add_custom_codes_to_json(JSON_file_path, custom_codes):
    """Inserts found custom code sections to their parent action"""

    for custom_code in custom_codes:
        add_custom_code_to_action_or_resource_json(JSON_file_path, custom_code["parent"], 'custom_codes', custom_code["custom_codes"])


def find_and_mark_empty_resources(JSON_obj):
    """Makes title of empty resources None.

    When a resource has only one action and they share names, the APIB declared an action witohut parent resource.
    """

    for resource_group in JSON_obj["resourceGroups"]:
        for resource in resource_group["resources"]:
            if len(resource["actions"]) == 1:
                if resource["actions"][0]["name"] == resource["name"]:
                    resource["name"] = None


def find_and_mark_empty_resources(JSON_file_path):
    """Makes a resource able to be ignored by emprtying its title. 

    When a resource has only one action and they share names, the APIB declared an action witohut parent resource.
    """
    
    json_content = ""

    with open(JSON_file_path, 'rU') as json_file:
        json_content = json.load(json_file)

    for resource_group in json_content["resourceGroups"]:
        for resource in resource_group["resources"]:
            if len(resource["actions"]) == 1:
                if resource["actions"][0]["name"] == resource["name"]:
                    resource["ignoreTOC"] = True
                else:
                    resource["ignoreTOC"] = False


    with open(JSON_file_path, 'w') as json_file:
        json.dump(json_content, json_file, indent=4)


def get_links_from_description(description):
    """Find via regex all the links in a description string"""

    link_regex = re.compile( "\[(?P<linkText>[^\(\)\[\]]*)\]\((?P<linkRef>[^\(\)\[\]]*)\)" )
    auto_link_regex = re.compile("\<(?P<linkRef>http[s]?://.*)\>")
    html_link_regex = re.compile("\<a href=\"(?P<linkRef>http[s]?://.*)\"\>(?P<linkText>[^\<]*)\</a>")

    links = []

    link_matches = link_regex.findall(description)
    if link_matches:
        for link_match in link_matches:
            link = {}
            link['title'] = link_match[0]
            link['url'] = link_match[1]

            links.append(link)
    else:
        link_matches = auto_link_regex.findall(description)
        if link_matches:
            for link_match in link_matches:
                link = {}
                link['title'] = link_match
                link['url'] = link_match

                links.append(link)
        else:
            link_matches = html_link_regex.findall(description)
            if link_matches:
                for link_match in link_matches:
                    link = {}
                    link['title'] = link_match[1]
                    link['url'] = link_match[0]

                    links.append(link)

    return links


def get_links_api_metadata(section):
    """Recursively get links from the api_metadata json section."""


    links = []
    links += get_links_from_description(section["body"])

    for subsection in section["subsections"]:
        links += get_links_api_metadata(subsection)

    return links


def get_markdown_links(json_content):
    """Returns a list with all the Markdown links present in a the json representation of a Markdown file."""

    links = []
    # Abstract
    links += get_links_from_description(json_content["description"])

    # API Metadata
    links += get_links_api_metadata(json_content["api_metadata"])

    # API specification
    for resource_group in json_content["resourceGroups"]:
        links += get_links_from_description(resource_group["description"])

        for resource in resource_group["resources"]:
            links += get_links_from_description(resource["description"])

            for action in resource["actions"]:
                links += get_links_from_description(action["description"])

                for example in action["examples"]:
                    for request in example["requests"]:
                        links += get_links_from_description(request["description"])

                    for response in example["responses"]:
                        links += get_links_from_description(response["description"])

    return links


def add_reference_links_to_json(JSON_file_path):
    """Extract all the links from the JSON file and adds them back to the JSON.

    Arguments:
    JSON_file_path -- path to the JSON file where all the links will be extracted and added in a separate section.
    """
    json_content = ""

    with open(JSON_file_path, 'rU') as json_file:
        json_content = json.load(json_file)

    json_content['reference_links'] = get_markdown_links(json_content)

    with open(JSON_file_path, 'w') as json_file:
        json.dump(json_content, json_file, indent=4)


def add_nested_parameter_description_to_json(API_blueprint_file_path, JSON_file_path):
    """Extracts all nested description for`parameter values and adds them to the JSON.

    Arguments:
    API_specification_path -- path to the specification file where all the links will be extracted from.
    JSON_file_path -- path to the JSON file where all the links will be added.
    """
    json_content = ""

    nested_descriptions_list = apib_extra_parse_utils.get_nested_parameter_values_description(API_blueprint_file_path)

    for nested_description in nested_descriptions_list:
        for parameter in nested_description["parameters"]:
            for value in parameter["values"]:

                add_description_to_json_parameter_value(JSON_file_path, 
                                                        nested_description["parent"], 
                                                        parameter["name"],
                                                        value["name"],
                                                        value["description"])


def escape_requests_responses_json(JSON_file_path):
    """Identifies when the body of a request or response uses an XML like type and escapes the '<' for browser rendering.

    Arguments:
    JSON_file_path -- path to the JSON file where requests and responses with XML like body will be escaped.
    """
    json_content = ""

    with open(JSON_file_path, 'rU') as json_file:
        json_content = json.load(json_file)

    for resource_group in json_content["resourceGroups"]:
        for resource in resource_group["resources"]:
            for action in resource["actions"]:
                for example in action["examples"]:

                    for request in example["requests"]:
                        if request["body"]:
                            request["body"] = request["body"].replace("<", "&lt;")
                            if not "sections" in request["content"][0]:
                                request["content"][0]["content"] = request["content"][0]["content"].replace("<", "&lt;")

                    for response in example["responses"]:
                        if response["body"]:
                            response["body"] = response["body"].replace("<", "&lt;")
                            if not "sections" in response["content"][0]:
                                response["content"][0]["content"] = response["content"][0]["content"].replace("<", "&lt;")

    with open(JSON_file_path, 'w') as json_file:
        json.dump(json_content, json_file, indent=4)


def render_description(JSON_file_path):
    """Escaping ampersand symbol form URIs.

    Arguments:
    JSON_file_path -- path to the JSON file where the ampersand will be be escaped in URIs.
    """
    json_content = ""

    with open(JSON_file_path, 'rU') as json_file:
        json_content = json.load(json_file)

    try:
        json_content["description"] = markdown.markdown( json_content["description"].decode('utf-8'), extensions=['markdown.extensions.tables','markdown.extensions.fenced_code'] )
    except UnicodeEncodeError as error:
        json_content["description"] = markdown.markdown( json_content["description"], extensions=['markdown.extensions.tables','markdown.extensions.fenced_code'] )

    with open(JSON_file_path, 'w') as json_file:
        json.dump(json_content, json_file, indent=4)


def escape_ampersand_uri_templates(JSON_file_path):
    """Renders the description of the API spscification to display it properly.

    Arguments:
    JSON_file_path -- path to the JSON file where the description will be rendered.
    """
    json_content = ""
    json_file_write_path = JSON_file_path + ".aux"
    URI_regex = re.compile("^([\s\t]*)\"uriTemplate\":[ ]\"(.*)\"$")

    with open(JSON_file_path, 'rU') as json_file_input, open(json_file_write_path, 'w') as json_file_output:
        for line in json_file_input:

            URI_match = URI_regex.match(line)
            if URI_match:
                line = line.replace(URI_match.group(2), URI_match.group(2).replace('&', '&amp;'))

            json_file_output.write(line)

    shutil.move(json_file_write_path, JSON_file_path)


def generate_resources_and_action_ids(JSON_file_path):
    """Generate an ID for every resource and action in the given JSON file

    Arguments:
    JSON_file_path - path to the JSON file containing the API parsed definition"""
    json_content = ""

    with open(JSON_file_path, 'rU') as json_file:
        json_content = json.load(json_file)

    for resource_group in json_content["resourceGroups"]:
        for resource in resource_group["resources"]:
            if len( resource["name"] ) > 0:
                resource["id"] = 'resource_' + slugify( resource["name"], '-' )
            else:
                resource["id"] = 'resource_' + slugify( resource["uriTemplate"], '-' )

            for action in resource["actions"]:
                if len( action["name"] ) > 0:
                    action["id"] = 'action_' + slugify( action["name"],'-' )
                else:
                    if len( action["attributes"]["uriTemplate"] ) > 0:
                        action["id"] = 'action_' + slugify( action["attributes"]["uriTemplate"], '-' )
                    else:
                        if resource["ignoreTOC"] == True:
                            action["id"] = 'action_' + slugify( resource["uriTemplate"] + action["method"], '-' )
                        else:
                            action["id"] = 'action_' + slugify( resource["name"] + action["method"], '-' )

    with open(JSON_file_path, 'w') as json_file:
        json.dump(json_content, json_file, indent=4)


def remove_redundant_spaces(JSON_file_path):
    """Remove redundant spaces from names of resources and actions

    Arguments:
    JSON_file_path - path to the JSON file containing the API parsed definition"""
    json_content = ""

    with open(JSON_file_path, 'rU') as json_file:
        json_content = json.load(json_file)

    for resource_group in json_content["resourceGroups"]:
        resource_group["name"] = re.sub( " +", " ", resource_group["name"] )
        for resource in resource_group["resources"]:
            resource["name"] = re.sub( " +", " ", resource["name"] )
            for action in resource["actions"]:
                action["name"] = re.sub( " +", " ", action["name"] )

    with open(JSON_file_path, 'w') as json_file:
        json.dump(json_content, json_file, indent=4)


def render_api_specification(API_specification_path, template_path, dst_dir_path, clear_temporal_dir=True, cover=None):
    """Renders an API specification using a template and saves it to destination directory.
    
    Arguments: 
    API_specification_path -- Path to API Blueprint specification
    template_path -- The Jinja2 template path
    dst_dir_path -- Path to save the compiled site
    clear_temporal_dir -- Flag to clear temporary files generated by the script  
    """

    temp_dir_path = "/var/tmp/fiware_api_blueprint_renderer_tmp"

    API_specification_file_name = os.path.splitext(os.path.basename(API_specification_path))[0]


    API_extra_sections_file_path = os.path.join(temp_dir_path, API_specification_file_name + '.extras')
    API_blueprint_file_path = os.path.join(temp_dir_path + '/' + API_specification_file_name + '.apib')
    API_blueprint_JSON_file_path = os.path.join(temp_dir_path + '/' + API_specification_file_name + '.json')
    
    create_directory_if_not_exists(temp_dir_path)
    separate_extra_sections_and_api_blueprint(API_specification_path, API_extra_sections_file_path, API_blueprint_file_path)

    parser_api_blueprint(API_blueprint_file_path, API_blueprint_JSON_file_path)
    add_metadata_to_json(parse_meta_data(API_extra_sections_file_path), API_blueprint_JSON_file_path)
    add_nested_parameter_description_to_json(API_blueprint_file_path, API_blueprint_JSON_file_path)
    parser_json_descriptions(API_blueprint_JSON_file_path)
    parser_json_data_structures(API_blueprint_JSON_file_path)
    find_and_mark_empty_resources(API_blueprint_JSON_file_path)
    render_description(API_blueprint_JSON_file_path)
    escape_requests_responses_json(API_blueprint_JSON_file_path)
    escape_ampersand_uri_templates(API_blueprint_JSON_file_path)
    generate_resources_and_action_ids(API_blueprint_JSON_file_path)
    remove_redundant_spaces(API_blueprint_JSON_file_path)
    add_reference_links_to_json(API_blueprint_JSON_file_path)

    add_is_pdf_metadata_to_json(cover is not None, API_blueprint_JSON_file_path)

    render_api_blueprint(template_path, API_blueprint_JSON_file_path, dst_dir_path)

    if (cover is not None): #cover needed for pdf
        #rename json
        cover_json_path = os.path.join( dst_dir_path + '/' + 'cover' + '.json' )
        shutil.move(API_blueprint_JSON_file_path, cover_json_path)
        render_api_blueprint( cover, cover_json_path, dst_dir_path )
        shutil.move(cover_json_path, API_blueprint_JSON_file_path)
        return

    if( clear_temporal_dir == True ):
        clear_directory( temp_dir_path )


def main():   
    
    usage = "Usage: \n\t" + sys.argv[0] + " -i <api-spec-path> -o <dst-dir> [--pdf] [--no-clear-temp-dir] [--template]"
    
    default_theme = os.path.dirname(__file__)+"/../themes/default_theme/api-specification.tpl"
    pdt_template_path= os.path.dirname(__file__)+"/../themes/default_theme/api-specification-pdf.tpl"
    cover_template_path= os.path.dirname(__file__)+"/../themes/default_theme/cover.tpl"
    template_path= default_theme
    clear_temporal_dir = True
    API_specification_path = None
    dst_dir_path = None
    temp_pdf_path = "/var/tmp/fiware_api_blueprint_renderer_tmp_pdf/"
    pdf = False

    try:
        opts, args = getopt.getopt(sys.argv[1:],"hi:o:ct:",["ifile=","odir=","no-clear-temp-dir","template=","pdf"])
    except getopt.GetoptError:
      print usage
      sys.exit(2)
    for opt, arg in opts:
        if opt == '-h':
            print usage
            sys.exit()
        elif opt in ("-i", "--input"):
            API_specification_path = arg
        elif opt in ("-o", "--output"):
            dst_dir_path = arg
        elif opt in ("-t", "--template"):
            template_path = arg
        elif opt in ("-c", "--no-clear-temp-dir"):
            clear_temporal_dir = False
        elif opt in ("--pdf"):
            pdf = True
            #if no template is specified, uses the default pdf template
            if not ('-t' in zip(*opts)[0] or '--template' in zip(*opts)[0]):
                template_path = pdt_template_path


    if API_specification_path is None:
        print "API specification file must be specified"
        print usage
        sys.exit(3)

    if dst_dir_path is None:
        print "Destination directory must be specified"
        print usage
        sys.exit(4)

    if pdf:
        create_directory_if_not_exists(temp_pdf_path)
        rendered_HTML_filename = os.path.splitext(os.path.basename(API_specification_path))[0]
        rendered_HTML_path = os.path.join(temp_pdf_path, rendered_HTML_filename + ".html")
        rendered_HTML_cover = os.path.join(temp_pdf_path, "cover" + ".html")

        if ".pdf" not in dst_dir_path:
            create_directory_if_not_exists(dst_dir_path)
            dst_dir_path = os.path.join(dst_dir_path, rendered_HTML_filename + ".pdf")

        render_api_specification(API_specification_path, template_path, temp_pdf_path, clear_temporal_dir, cover_template_path)
        call( ["wkhtmltopdf", '-d', '125', '--page-size','A4', "page", "file://"+rendered_HTML_cover ,"toc" ,"page", "file://"+rendered_HTML_path, '--footer-center', "Page [page]",'--footer-font-size', '8', '--footer-spacing', '3','--run-script', "setInterval(function(){if(document.readyState=='complete') window.status='done';},100)", "--window-status", "done", dst_dir_path ])
    else:
        create_directory_if_not_exists( dst_dir_path )
        render_api_specification( API_specification_path, template_path, dst_dir_path, clear_temporal_dir, None)
    sys.exit(0)


if __name__ == "__main__":
    main()
